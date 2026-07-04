"""Custom grouped layer panel (replaces Folium's default LayerControl).

The default control renders all layers as a flat radio list. With 10+ tile
layers grouped into ~5 categories, a flat list gets hard to scan. This
panel groups them under section headers using the same dark styling as
the legend + stats panel.

Single radio across the whole panel = mutually-exclusive layer selection
(same semantics as Leaflet "base layers").
"""

from __future__ import annotations

import json

from heatmap.basemaps import available_basemaps

# (group, display_name, subdir, legend_id, visible_by_default)
# Single source of truth for the layer list. render.py consumes the (display,
# subdir, visible) triple to create TileLayers; this module emits the panel
# HTML + JS using all five fields.
LAYERS: list[tuple[str, str, str, str, bool]] = [
    ("Frequency", "Top routes", "count", "legend-frequency", False),
    ("Frequency", "All routes", "count_log", "legend-frequency-log", True),
    ("Time", "Recency", "recency", "legend-recency", False),
    ("Time", "Freshness 3 mo", "freshness_3mo", "legend-freshness-3mo", False),
    ("Time", "Freshness 12 mo", "freshness", "legend-freshness", False),
    ("Time", "Freshness 36 mo", "freshness_36mo", "legend-freshness-36mo", False),
    ("Pace", "Average", "speed", "legend-pace-avg", False),
    ("Heart rate", "Average", "hr", "legend-heart-rate-avg", False),
    ("Elevation", "Steepness", "grad", "legend-gradient", False),
    ("Elevation", "Up vs down", "elev", "legend-elev-change", False),
    ("Elevation", "Hill training", "hill", "legend-hill", False),
]


_PANEL_CSS = """
<style>
  #layer-panel {
    position: fixed; top: 10px; right: 10px; z-index: 9999;
    background: rgba(15,15,15,0.88);
    padding: 11px 14px 13px; border-radius: 9px;
    color: #ddd; font-family: sans-serif; font-size: 12px;
    line-height: 1.4; min-width: 180px;
    border: 1px solid rgba(255,255,255,0.10);
    box-shadow: 0 2px 8px rgba(0,0,0,0.6);
  }
  #layer-panel .group-title {
    font-weight: 700; font-size: 10px; letter-spacing: 0.08em;
    color: #888; text-transform: uppercase;
    margin: 8px 0 3px;
  }
  #layer-panel .group-title:first-child { margin-top: 0; }
  #layer-panel .divider {
    border-top: 1px solid rgba(255,255,255,0.08);
    margin: 8px 0 4px;
  }
  #layer-panel label {
    display: flex; align-items: center; gap: 6px;
    color: #eee; font-weight: 600; margin: 2px 0;
    cursor: pointer;
  }
  #layer-panel input[type=radio] { accent-color: #eee; cursor: pointer; }
</style>
"""


def _panel_html(profiles: list[str], default_profile: str) -> str:
    rows = []

    # Activity profile (hidden if only one profile, no point cluttering the UI)
    if len(profiles) > 1:
        rows.append('<div class="group-title">Activity</div>')
        for p in profiles:
            checked = "checked" if p == default_profile else ""
            label = p.replace("_", " ").title()
            rows.append(f'<label><input type="radio" name="profile" value="{p}" {checked}><span>{label}</span></label>')
        rows.append('<div class="divider"></div>')

    rows.append('<div class="group-title">Basemap</div>')
    for i, b in enumerate(available_basemaps()):
        checked = "checked" if i == 0 else ""
        rows.append(
            f'<label><input type="radio" name="basemap" value="{b.key}" {checked}><span>{b.label}</span></label>'
        )
    rows.append('<div class="divider"></div>')

    last_group = None
    for group, display, subdir, _legend_id, visible in LAYERS:
        if group != last_group:
            rows.append(f'<div class="group-title">{group}</div>')
            last_group = group
        checked = "checked" if visible else ""
        rows.append(
            f'<label><input type="radio" name="heatmap-layer" value="{subdir}" {checked}><span>{display}</span></label>'
        )
    return '<div id="layer-panel">\n' + "\n".join(rows) + "\n</div>"


def _layer_meta_json() -> str:
    """Subdir → {legendId} so the JS can also swap the legend section."""
    return json.dumps({subdir: {"legend": legend_id} for _, _, subdir, legend_id, _ in LAYERS})


def _basemap_meta_json() -> str:
    """Key → url_match substring so the JS can locate each TileLayer."""
    return json.dumps({b.key: b.url_match for b in available_basemaps()})


_PANEL_JS_TMPL = """
<script>
(function() {
  var LAYER_META = __LAYER_META__;
  var BASEMAP_META = __BASEMAP_META__;

  function findMap() {
    for (var k in window) {
      try { if (window[k] instanceof L.Map) return window[k]; } catch(e) {}
    }
    return null;
  }

  function indexHeatmapLayers() {
    // Two-level: { profile: { layer: TileLayer } }.
    // URL template at build time is `tiles/<profile>/<layer>/{z}/{x}/{y}.png`.
    var by = {};
    for (var k in window) {
      var v;
      try { v = window[k]; } catch (e) { continue; }
      if (!v || !(v instanceof L.TileLayer) || !v._url) continue;
      var m = v._url.match(/^tiles\\/([^/]+)\\/([^/]+)\\//);
      if (m) {
        var p = m[1], l = m[2];
        by[p] = by[p] || {};
        by[p][l] = v;
      }
    }
    return by;
  }

  function indexBasemaps() {
    var byName = {};
    for (var k in window) {
      var v;
      try { v = window[k]; } catch (e) { continue; }
      if (!v || !(v instanceof L.TileLayer) || !v._url) continue;
      var url = v._url;
      Object.keys(BASEMAP_META).forEach(function(key) {
        if (url.indexOf(BASEMAP_META[key]) >= 0) byName[key] = v;
      });
    }
    return byName;
  }

  function hideAllHeatmap(mapObj, byProfile) {
    Object.keys(byProfile).forEach(function(p) {
      Object.keys(byProfile[p]).forEach(function(l) {
        if (mapObj.hasLayer(byProfile[p][l])) mapObj.removeLayer(byProfile[p][l]);
      });
    });
  }

  function showHeatmap(mapObj, byProfile, profile, layer) {
    hideAllHeatmap(mapObj, byProfile);
    if (byProfile[profile] && byProfile[profile][layer]) {
      byProfile[profile][layer].addTo(mapObj);
    }
  }

  function showLegend(name) {
    Object.keys(LAYER_META).forEach(function(k) {
      var id = LAYER_META[k].legend;
      var el = document.getElementById(id);
      if (el) el.style.display = (k === name) ? "block" : "none";
    });
  }

  function setup() {
    var mapObj = findMap();
    if (!mapObj) { setTimeout(setup, 100); return; }
    var layersByProfile = indexHeatmapLayers();
    var basemaps = indexBasemaps();

    var profileInput = document.querySelector('input[name="profile"]:checked');
    var activeProfile = profileInput ? profileInput.value
                                     : Object.keys(layersByProfile)[0];

    var layerInput = document.querySelector('input[name="heatmap-layer"]:checked');
    var activeLayer = layerInput ? layerInput.value : "count_log";

    function applyActive() {
      showHeatmap(mapObj, layersByProfile, activeProfile, activeLayer);
      showLegend(activeLayer);
      if (window.__statsPanelSetProfile__) window.__statsPanelSetProfile__(activeProfile);
    }

    applyActive();

    document.querySelectorAll('input[name="heatmap-layer"]').forEach(function(input) {
      input.addEventListener('change', function() {
        activeLayer = input.value;
        applyActive();
      });
    });
    document.querySelectorAll('input[name="profile"]').forEach(function(input) {
      input.addEventListener('change', function() {
        activeProfile = input.value;
        applyActive();
      });
    });

    // Basemap radios — show one, hide the other. Layers must sit below the
    // heatmap tiles; setZIndex keeps the chosen basemap behind.
    function setBasemap(name) {
      Object.keys(basemaps).forEach(function(k) {
        if (mapObj.hasLayer(basemaps[k])) mapObj.removeLayer(basemaps[k]);
      });
      if (basemaps[name]) {
        basemaps[name].addTo(mapObj);
        if (basemaps[name].setZIndex) basemaps[name].setZIndex(0);
      }
    }
    var bInit = document.querySelector('input[name="basemap"]:checked');
    if (bInit) setBasemap(bInit.value);
    document.querySelectorAll('input[name="basemap"]').forEach(function(input) {
      input.addEventListener('change', function() { setBasemap(input.value); });
    });
  }

  document.addEventListener("DOMContentLoaded", setup);
})();
</script>
"""


def build_layer_panel_html(profiles: list[str], default_profile: str) -> str:
    js = _PANEL_JS_TMPL.replace("__LAYER_META__", _layer_meta_json())
    js = js.replace("__BASEMAP_META__", _basemap_meta_json())
    return _PANEL_CSS + _panel_html(profiles, default_profile) + js
