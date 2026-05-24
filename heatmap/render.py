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

from heatmap.assets import EXCLUSIVE_OVERLAY_JS
from heatmap.assets import LAYER_CONTROL_CSS
from heatmap.legend import build_legend_html

if TYPE_CHECKING:
    from heatmap.config import Config
    from heatmap.tiles import PyramidResult

log = logging.getLogger(__name__)


# (display_name, layer_subdir, visible_by_default)
_LAYER_SPEC: list[tuple[str, str, bool]] = [
    ("Frequency (linear)", "count", False),
    ("Frequency (log)", "count_log", True),
    ("Pace (average)", "speed", False),
    ("Heart rate (average)", "hr", False),
    ("Gradient (absolute)", "grad", False),
    ("Gradient (change)", "elev", False),
]


# 1x1 transparent PNG, used as the fallback for missing tiles so Leaflet
# doesn't show the broken-image icon over sparse-pyramid gaps.
_TRANSPARENT_PIXEL_URI = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def _add_basemap(m: folium.Map) -> None:
    folium.TileLayer(
        "CartoDB.DarkMatterNoLabels",
        name="Basemap",
        control=False,
        show=True,
        no_wrap=True,
        error_tile_url=_TRANSPARENT_PIXEL_URI,
    ).add_to(m)


def _add_raster_tilelayers(
    m: folium.Map,
    pyramid: PyramidResult,
    opacity: float,
) -> None:
    """Add one Folium TileLayer per heatmap layer.

    `overlay=False` puts these in the "base layers" section of the LayerControl,
    rendering as native radio buttons (Leaflet enforces mutual exclusion).
    The actual basemap stays out of the control entirely (`control=False`).

    `bounds` restricts tile requests to the data's lat/lon bbox — Leaflet
    won't fetch tiles outside it, cutting 404 noise in the dev server log.

    All four zoom limits collapse to `pyramid.{min,max}_zoom`. See module
    docstring for the rationale.
    """
    z_min, z_max = pyramid.min_zoom, pyramid.max_zoom
    for display_name, subdir, visible in _LAYER_SPEC:
        folium.TileLayer(
            tiles=f"tiles/{subdir}/{{z}}/{{x}}/{{y}}.png",
            attr="Strava heatmap",
            name=display_name,
            overlay=False,
            control=True,
            min_zoom=z_min,
            max_zoom=z_max,
            min_native_zoom=z_min,
            max_native_zoom=z_max,
            tms=False,
            opacity=opacity,
            show=visible,
            no_wrap=True,
            bounds=pyramid.bounds_latlon,
            error_tile_url=_TRANSPARENT_PIXEL_URI,
        ).add_to(m)


def build_and_save(
    pyramid: PyramidResult,
    config: Config,
) -> str:
    """Assemble the Folium map with TileLayers, save HTML. Returns output path."""
    bounds = pyramid.bounds_latlon
    centre = pyramid.centre_latlon
    z_min, z_max = pyramid.min_zoom, pyramid.max_zoom

    # zoom_start is overridden by fit_bounds below but must be inside [z_min, z_max]
    # to keep Folium happy.
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
    _add_raster_tilelayers(m, pyramid, config.map_opacity)

    folium.LayerControl(collapsed=False).add_to(m)
    m.get_root().html.add_child(folium.Element(LAYER_CONTROL_CSS))
    m.get_root().html.add_child(
        folium.Element(
            build_legend_html(
                speed_range=pyramid.speed_range,
                hr_range=pyramid.hr_range,
                grad_range=pyramid.grad_range,
                count_max=pyramid.count_max,
            )
        )
    )
    m.get_root().html.add_child(folium.Element(EXCLUSIVE_OVERLAY_JS))

    output_path = config.output_html_path()
    output_path.parent.mkdir(exist_ok=True)
    m.save(str(output_path))
    log.info("Saved: %s", output_path)
    log.info("Serve: cd %s && python -m http.server 8000", output_path.parent)
    log.info("Open:  http://localhost:8000/%s", output_path.name)
    return str(output_path)
