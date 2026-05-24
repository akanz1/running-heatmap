from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import folium
from pyproj import Transformer

from heatmap.assets import EXCLUSIVE_OVERLAY_JS
from heatmap.assets import LAYER_CONTROL_CSS
from heatmap.colormaps import CMAP_COUNT
from heatmap.colormaps import CMAP_ELEV
from heatmap.colormaps import CMAP_HR
from heatmap.colormaps import CMAP_SPEED
from heatmap.encoding import colormap_to_uri
from heatmap.encoding import rgba_with_alpha_uri
from heatmap.encoding import white_alpha_uri
from heatmap.legend import build_legend_html

if TYPE_CHECKING:
    from heatmap.config import Config
    from heatmap.raster import NormalizedLayers
    from heatmap.raster import RasterGrids

log = logging.getLogger(__name__)


def _bounds_and_centre(grids: RasterGrids) -> tuple[list[list[float]], list[float]]:
    from_wm = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    lon_nw, lat_nw = from_wm.transform(grids.x_min_wm, grids.y_max_wm)
    lon_se, lat_se = from_wm.transform(grids.x_max_wm, grids.y_min_wm)
    bounds = [[lat_se, lon_nw], [lat_nw, lon_se]]
    centre = [(lat_nw + lat_se) / 2, (lon_nw + lon_se) / 2]
    return bounds, centre


def _encode_layers(layers: NormalizedLayers) -> list[tuple[str, str, bool]]:
    """Encode each normalised layer to a PNG data URI. Returns (name, uri, default_visible)."""
    return [
        ("Frequency (linear)", colormap_to_uri(layers.count_norm, CMAP_COUNT), True),
        ("Frequency (log)", colormap_to_uri(layers.count_log_norm, CMAP_COUNT), False),
        ("Pace (average)", rgba_with_alpha_uri(layers.speed_norm, layers.alpha_speed, CMAP_SPEED), False),
        ("Heart rate (average)", rgba_with_alpha_uri(layers.hr_norm, layers.alpha_hr, CMAP_HR), False),
        ("Gradient (absolute)", white_alpha_uri(layers.alpha_grad), False),
        ("Gradient (change)", rgba_with_alpha_uri((layers.elev_norm + 1) / 2, layers.alpha_elev, CMAP_ELEV), False),
    ]


def _add_basemap(m: folium.Map) -> None:
    folium.TileLayer(
        "CartoDB.DarkMatterNoLabels",
        name="Basemap",
        control=False,
        show=True,
    ).add_to(m)


def _add_raw_tracks(m: folium.Map, tracks: list[tuple[str, list[list]]]) -> None:
    group = folium.FeatureGroup(name="Raw GPS tracks", show=False)
    for label, pts in tracks:
        folium.PolyLine(
            locations=[(p[0], p[1]) for p in pts],
            color="#fc4c02",
            weight=1,
            opacity=0.4,
            tooltip=label,
        ).add_to(group)
    group.add_to(m)


def _add_raster_layers(
    m: folium.Map,
    encoded: list[tuple[str, str, bool]],
    bounds: list[list[float]],
    opacity: float,
) -> None:
    for name, uri, visible in encoded:
        fg = folium.FeatureGroup(name=name, show=visible)
        folium.raster_layers.ImageOverlay(
            image=uri,
            bounds=bounds,
            opacity=opacity,
            interactive=False,
            cross_origin=False,
            zindex=1,
        ).add_to(fg)
        fg.add_to(m)


def build_and_save(
    grids: RasterGrids,
    normalized: NormalizedLayers,
    tracks: list[tuple[str, list[list]]],
    config: Config,
) -> str:
    """Assemble the Folium map, embed all layers, save HTML. Returns output path."""
    bounds, centre = _bounds_and_centre(grids)

    log.info("Rendering layers…")
    encoded = _encode_layers(normalized)
    log.info("Done — %d layers encoded", len(encoded))

    m = folium.Map(location=centre, zoom_start=14, tiles=None, control_scale=True)
    _add_basemap(m)
    _add_raw_tracks(m, tracks)
    _add_raster_layers(m, encoded, bounds, config.map_opacity)

    folium.LayerControl(collapsed=False).add_to(m)
    m.get_root().html.add_child(folium.Element(LAYER_CONTROL_CSS))
    m.get_root().html.add_child(folium.Element(build_legend_html(normalized)))
    m.get_root().html.add_child(folium.Element(EXCLUSIVE_OVERLAY_JS))

    output_path = config.output_html_path()
    output_path.parent.mkdir(exist_ok=True)
    m.save(str(output_path))
    log.info("Saved: %s", output_path)
    log.info("Open:  file://%s", output_path.resolve())
    return str(output_path)
