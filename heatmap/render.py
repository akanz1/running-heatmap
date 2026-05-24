from __future__ import annotations

import base64
import os
from io import BytesIO
from pathlib import Path

import folium
import numpy as np
from PIL import Image
from pyproj import Transformer

from heatmap.colormaps import CMAP_COUNT
from heatmap.colormaps import CMAP_ELEV
from heatmap.colormaps import CMAP_HR
from heatmap.colormaps import CMAP_SPEED
from heatmap.config import Config
from heatmap.raster import NormalizedLayers
from heatmap.raster import RasterGrids

# --------------------------------------------------------------------------- #
# Image encoding helpers
# --------------------------------------------------------------------------- #


def _to_uri(rgba_u8: np.ndarray) -> str:
    buf = BytesIO()
    Image.fromarray(rgba_u8, mode="RGBA").save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _count_uri(norm: np.ndarray) -> str:
    return _to_uri((CMAP_COUNT(norm) * 255).clip(0, 255).astype(np.uint8))


def _rgba_uri(rgb_norm: np.ndarray, alpha_norm: np.ndarray, cmap) -> str:
    arr = cmap(rgb_norm).copy()
    arr[:, :, 3] = alpha_norm
    return _to_uri((arr * 255).clip(0, 255).astype(np.uint8))


def _white_uri(alpha_norm: np.ndarray) -> str:
    h, w = alpha_norm.shape
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[:, :, :3] = 255
    arr[:, :, 3] = (alpha_norm * 255).clip(0, 255).astype(np.uint8)
    return _to_uri(arr)


# --------------------------------------------------------------------------- #
# Legend helpers
# --------------------------------------------------------------------------- #


def _cmap_to_css(cmap, n: int = 14) -> str:
    stops = []
    for i in range(n):
        t = i / (n - 1)
        r, g, b, a = cmap(t)
        stops.append(f"rgba({int(r * 255)},{int(g * 255)},{int(b * 255)},{a:.2f})")
    return f"linear-gradient(to right, {', '.join(stops)})"


def _pace_str(ms: float) -> str:
    secs = 1000 / ms
    return f"{int(secs // 60)}:{int(secs % 60):02d}/km"


def _legend_row(
    row_id: str,
    title: str,
    grad_css: str,
    label_lo: str,
    label_hi: str,
    visible: bool = False,
) -> str:
    display = "block" if visible else "none"
    return f"""
    <div id="{row_id}" style="display:{display}">
      <div style="font-weight:600;margin-bottom:3px;color:#eee">{title}</div>
      <div style="height:10px;border-radius:3px;background:{grad_css};
                  border:1px solid rgba(255,255,255,0.08)"></div>
      <div style="display:flex;justify-content:space-between;
                  margin-top:3px;color:#aaa;font-size:11px">
        <span>{label_lo}</span><span>{label_hi}</span>
      </div>
    </div>"""


# --------------------------------------------------------------------------- #
# Static HTML/JS/CSS fragments
# --------------------------------------------------------------------------- #

_LAYER_CONTROL_CSS = """
<style>
  .leaflet-control-layers {
    background: rgba(15,15,15,0.88) !important;
    border: 1px solid rgba(255,255,255,0.10) !important;
    border-radius: 9px !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.6) !important;
    color: #ddd !important;
    font-family: sans-serif !important;
    font-size: 12px !important;
  }
  .leaflet-control-layers-expanded { padding: 11px 14px 13px !important; }
  .leaflet-control-layers label {
    color: #eee !important;
    font-weight: 600 !important;
    display: flex !important;
    align-items: center !important;
    gap: 6px !important;
    margin: 4px 0 !important;
  }
  .leaflet-control-layers-separator {
    border-color: rgba(255,255,255,0.12) !important;
    margin: 6px 0 !important;
  }
  .leaflet-control-layers-toggle {
    background-color: rgba(15,15,15,0.88) !important;
    border-radius: 9px !important;
    border: 1px solid rgba(255,255,255,0.10) !important;
  }
</style>
"""

_EXCLUSIVE_JS = """
<script>
(function() {
    var exclusiveNames = [
        "Frequency (linear)", "Frequency (log)",
        "Pace (average)", "Heart rate (average)",
        "Gradient (absolute)", "Gradient (change)"
    ];
    var legendIds = {
        "Frequency (linear)":   "legend-frequency",
        "Frequency (log)":      "legend-frequency-log",
        "Pace (average)":       "legend-pace-avg",
        "Heart rate (average)": "legend-heart-rate-avg",
        "Gradient (absolute)":  "legend-gradient",
        "Gradient (change)":    "legend-elev-change"
    };
    function showLegend(activeName) {
        Object.keys(legendIds).forEach(function(name) {
            var el = document.getElementById(legendIds[name]);
            if (el) el.style.display = (name === activeName) ? "block" : "none";
        });
    }
    function setup() {
        var mapObj = null, overlays = null;
        for (var k in window) {
            try {
                if (!mapObj   && window[k] instanceof L.Map) mapObj = window[k];
                if (!overlays && window[k] && window[k].overlays && window[k].base_layers)
                    overlays = window[k].overlays;
            } catch(e) {}
        }
        if (!mapObj || !overlays) { setTimeout(setup, 100); return; }
        mapObj.on('overlayadd', function(e) {
            if (!exclusiveNames.includes(e.name)) return;
            exclusiveNames.forEach(function(name) {
                if (name !== e.name && overlays[name] && mapObj.hasLayer(overlays[name]))
                    mapObj.removeLayer(overlays[name]);
            });
            showLegend(e.name);
        });
    }
    document.addEventListener('DOMContentLoaded', setup);
})();
</script>
"""


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #


def build_and_save(
    grids: RasterGrids,
    normalized: NormalizedLayers,
    tracks: list[tuple[str, list[list]]],
    config: Config,
) -> str:
    """Assemble the Folium map, embed all layers, save HTML. Returns output path."""
    from_wm = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    lon_nw, lat_nw = from_wm.transform(grids.x_min_wm, grids.y_max_wm)
    lon_se, lat_se = from_wm.transform(grids.x_max_wm, grids.y_min_wm)
    bounds = [[lat_se, lon_nw], [lat_nw, lon_se]]
    centre = [(lat_nw + lat_se) / 2, (lon_nw + lon_se) / 2]

    s_lo, s_hi = normalized.speed_range
    hr_lo, hr_hi = normalized.hr_range
    g_lo, g_hi = normalized.grad_range

    print("Rendering layers…")
    layers: list[tuple[str, str, bool]] = [
        ("Frequency (linear)", _count_uri(normalized.count_norm), True),
        ("Frequency (log)", _count_uri(normalized.count_log_norm), False),
        (
            "Pace (average)",
            _rgba_uri(normalized.speed_norm, normalized.alpha_speed, CMAP_SPEED),
            False,
        ),
        (
            "Heart rate (average)",
            _rgba_uri(normalized.hr_norm, normalized.alpha_hr, CMAP_HR),
            False,
        ),
        ("Gradient (absolute)", _white_uri(normalized.alpha_grad), False),
        (
            "Gradient (change)",
            _rgba_uri((normalized.elev_norm + 1) / 2, normalized.alpha_elev, CMAP_ELEV),
            False,
        ),
    ]
    print(f"Done — {len(layers)} layers encoded.")

    freq_css = _cmap_to_css(CMAP_COUNT)
    pace_css = _cmap_to_css(CMAP_SPEED)
    hr_css = _cmap_to_css(CMAP_HR)

    legend_html = f"""
<div id="heatmap-legend" style="
    position:fixed; bottom:28px; right:10px; z-index:9999;
    background:rgba(15,15,15,0.88);
    padding:13px 16px 14px; border-radius:9px;
    color:#ddd; font-family:sans-serif; font-size:12px;
    min-width:210px; line-height:1.4;
    border:1px solid rgba(255,255,255,0.10);
    box-shadow:0 2px 8px rgba(0,0,0,0.6);
">
  {
        _legend_row(
            "legend-frequency",
            "Frequency (linear)",
            freq_css,
            "1 pass",
            f"{int(normalized.count_max)} passes",
            visible=True,
        )
    }
  {
        _legend_row(
            "legend-frequency-log",
            "Frequency (log)",
            freq_css,
            "1 pass",
            f"{int(normalized.count_max)} passes (log scale)",
        )
    }
  {
        _legend_row(
            "legend-pace-avg",
            "Pace (average)",
            pace_css,
            _pace_str(s_lo),
            _pace_str(s_hi),
        )
    }
  {
        _legend_row(
            "legend-heart-rate-avg",
            "Heart rate (average)",
            hr_css,
            f"{hr_lo:.0f} bpm",
            f"{hr_hi:.0f} bpm",
        )
    }
  {
        _legend_row(
            "legend-gradient",
            "Gradient (absolute)",
            "linear-gradient(to right, rgba(0,0,0,0), rgba(255,255,255,1))",
            f"{g_lo * 100:.1f}%",
            f"{g_hi * 100:.1f}% grade",
        )
    }
  {
        _legend_row(
            "legend-elev-change",
            "Gradient (change)",
            _cmap_to_css(CMAP_ELEV),
            "descending",
            "ascending",
        )
    }
</div>
"""

    m = folium.Map(location=centre, zoom_start=14, tiles=None, control_scale=True)
    folium.TileLayer(
        "CartoDB.DarkMatterNoLabels",
        name="Basemap",
        control=False,
        show=True,
    ).add_to(m)

    track_group = folium.FeatureGroup(name="Raw GPS tracks", show=False)
    for label, pts in tracks:
        folium.PolyLine(
            locations=[(p[0], p[1]) for p in pts],
            color="#fc4c02",
            weight=1,
            opacity=0.4,
            tooltip=label,
        ).add_to(track_group)
    track_group.add_to(m)

    for name, uri, visible in layers:
        fg = folium.FeatureGroup(name=name, show=visible)
        folium.raster_layers.ImageOverlay(
            image=uri,
            bounds=bounds,
            opacity=config.map_opacity,
            interactive=False,
            cross_origin=False,
            zindex=1,
        ).add_to(fg)
        fg.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    m.get_root().html.add_child(folium.Element(_LAYER_CONTROL_CSS))
    m.get_root().html.add_child(folium.Element(legend_html))
    m.get_root().html.add_child(folium.Element(_EXCLUSIVE_JS))

    output_path = config.output_html
    Path(output_path).parent.mkdir(exist_ok=True)
    m.save(output_path)
    print(f"Saved: {output_path}")
    print(f"Open:  file://{os.path.abspath(output_path)}")
    return output_path
