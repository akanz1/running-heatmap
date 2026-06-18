"""Folium map assembly: wraps the rendered tile pyramid in an HTML viewer.

## Zoom limits — a brief tour

Leaflet exposes four zoom-related knobs. We collapse them all to
``pyramid.{min,max}_zoom`` so they stay consistent:

| Knob                                  | Meaning                                                    |
|---------------------------------------|------------------------------------------------------------|
| ``L.Map.{min,max}Zoom``               | Slider / wheel limits — what zooms the *user* can reach    |
| ``L.tileLayer.{min,max}Zoom``         | Layer visibility window — hidden outside this range        |
| ``L.tileLayer.{min,max}NativeZoom``   | Where tile PNGs actually exist on disk — out of range,     |
|                                       | Leaflet up/downscales the nearest native tile              |

If all four equal ``pyramid.{min,max}_zoom``, the user can navigate exactly
the range we rendered, with no upscaling and no downscaling artifacts.

Folium gotcha: ``folium.Map(min_zoom=..., max_zoom=...)`` silently drops
those kwargs when ``tiles=None`` (they're routed to a non-existent default
tile layer). We assign ``m.options["minZoom"|"maxZoom"]`` directly so they
actually reach ``L.map()``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import folium

from heatmap.basemaps import available_basemaps
from heatmap.basemaps import resolved_url
from heatmap.layer_panel import build_layer_panel_html
from heatmap.layer_panel import LAYERS
from heatmap.legend import build_legend_html
from heatmap.stats_panel import build_stats_panel_html

if TYPE_CHECKING:
    from heatmap.config import Config
    from heatmap.stats_panel import StatsPanelData
    from heatmap.tiles import PyramidResult

log = logging.getLogger(__name__)


# Layer order + visibility come from layer_panel.LAYERS (single source of truth).


# 1x1 transparent PNG, used as the fallback for missing tiles so Leaflet
# doesn't show the broken-image icon over sparse-pyramid gaps.
_TRANSPARENT_PIXEL_URI = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def _add_basemap(m: folium.Map) -> None:
    """Register every available basemap; layer panel swaps which one is on."""
    for i, b in enumerate(available_basemaps()):
        folium.TileLayer(
            tiles=resolved_url(b),
            attr=b.attribution,
            name=f"basemap-{b.key}",
            control=False,
            show=(i == 0),  # first one (Dark) is the default
            no_wrap=True,
            max_zoom=b.max_zoom,
            max_native_zoom=b.max_zoom,
            error_tile_url=_TRANSPARENT_PIXEL_URI,
        ).add_to(m)


def _add_raster_tilelayers(
    m: folium.Map,
    pyramid_by_profile: dict[str, PyramidResult],
    default_profile: str,
    opacity: float,
) -> None:
    """Add one TileLayer per (profile, layer).

    `min/max_zoom` use the union across all profiles so every layer stays
    visible at every map zoom level — Leaflet auto up/downscales tiles
    outside that profile's native range. Otherwise a profile with a tighter
    data bbox would vanish at low zooms even though its tiles exist at the
    profile's own min_zoom.
    """
    union_min = min(p.min_zoom for p in pyramid_by_profile.values())
    union_max = max(p.max_zoom for p in pyramid_by_profile.values())
    for profile, pyramid in pyramid_by_profile.items():
        for _group, display_name, subdir, _legend_id, visible in LAYERS:
            folium.TileLayer(
                tiles=f"tiles/{profile}/{subdir}/{{z}}/{{x}}/{{y}}.png",
                attr="Strava heatmap",
                name=f"{profile}-{display_name}",
                overlay=True,
                control=False,
                min_zoom=union_min,
                max_zoom=union_max,
                min_native_zoom=pyramid.min_zoom,
                max_native_zoom=pyramid.max_zoom,
                tms=False,
                opacity=opacity,
                show=(visible and profile == default_profile),
                no_wrap=True,
                bounds=pyramid.bounds_latlon,
                error_tile_url=_TRANSPARENT_PIXEL_URI,
            ).add_to(m)


def build_and_save(
    pyramid_by_profile: dict[str, PyramidResult],
    config: Config,
    stats_by_profile: dict[str, StatsPanelData | None] | None = None,
) -> str:
    """Assemble the Folium map with multi-profile TileLayers, save HTML."""
    if not pyramid_by_profile:
        msg = "build_and_save: no profiles to render"
        raise ValueError(msg)

    profiles = list(pyramid_by_profile.keys())
    default_profile = profiles[0]
    default_pyramid = pyramid_by_profile[default_profile]

    # Initial view fits the UNION of all profiles, not just the default one —
    # otherwise switching to another profile can leave half its data off-screen.
    all_bounds = [p.bounds_latlon for p in pyramid_by_profile.values()]
    south = min(b[0][0] for b in all_bounds)
    west = min(b[0][1] for b in all_bounds)
    north = max(b[1][0] for b in all_bounds)
    east = max(b[1][1] for b in all_bounds)
    bounds = [[south, west], [north, east]]
    centre = [(south + north) / 2, (west + east) / 2]
    z_min = min(p.min_zoom for p in pyramid_by_profile.values())
    z_max = max(p.max_zoom for p in pyramid_by_profile.values())

    m = folium.Map(
        location=centre,
        zoom_start=max(z_min, min(z_max, 12)),
        tiles=None,
        control_scale=True,
        world_copy_jump=False,
    )
    m.options["minZoom"] = z_min
    m.options["maxZoom"] = z_max
    m.fit_bounds(bounds)

    _add_basemap(m)
    _add_raster_tilelayers(m, pyramid_by_profile, default_profile, config.map_opacity)

    m.get_root().html.add_child(
        folium.Element(build_layer_panel_html(profiles=profiles, default_profile=default_profile))
    )
    m.get_root().html.add_child(
        folium.Element(
            build_legend_html(
                speed_range=default_pyramid.speed_range,
                hr_range=default_pyramid.hr_range,
                grad_range=default_pyramid.grad_range,
                count_max=default_pyramid.count_max,
                elev_gain_hi=default_pyramid.elev_gain_hi,
                date_range_days=default_pyramid.date_range_days,
                recent_count_3mo_max=default_pyramid.recent_count_3mo_max,
                recent_count_max=default_pyramid.recent_count_max,
                recent_count_36mo_max=default_pyramid.recent_count_36mo_max,
            )
        )
    )

    stats = stats_by_profile or {}
    if any(stats.get(p) is not None for p in profiles):
        m.get_root().html.add_child(folium.Element(build_stats_panel_html(stats, default_profile)))

    output_path = config.output_html_path()
    output_path.parent.mkdir(exist_ok=True)
    m.save(str(output_path))
    log.info("Saved: %s  (profiles: %s)", output_path, ", ".join(profiles))
    log.info("Serve: make serve")
    log.info("Open:  http://localhost:8000/%s", output_path.name)
    return str(output_path)
