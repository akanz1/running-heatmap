"""Static CSS + JS injected into the Folium map.

Kept as string constants so render.py stays focused on assembly.
"""

from __future__ import annotations

LAYER_CONTROL_CSS = """
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

# Heatmap layers are configured as Leaflet "base layers" (overlay=False) so
# the LayerControl renders them as native radio buttons — Leaflet enforces
# mutual exclusion automatically. This script only syncs legend visibility
# when the active base layer changes.
EXCLUSIVE_OVERLAY_JS = """
<script>
(function() {
    var legendIds = {
        "Frequency (linear)":       "legend-frequency",
        "Frequency (log)":          "legend-frequency-log",
        "Pace (average)":           "legend-pace-avg",
        "Heart rate (average)":     "legend-heart-rate-avg",
        "Gradient (absolute)":      "legend-gradient",
        "Gradient (change)":        "legend-elev-change",
        "Recency":                  "legend-recency",
        "Freshness (last 12 mo)":   "legend-freshness"
    };
    function showLegend(activeName) {
        Object.keys(legendIds).forEach(function(name) {
            var el = document.getElementById(legendIds[name]);
            if (el) el.style.display = (name === activeName) ? "block" : "none";
        });
    }
    function setup() {
        var mapObj = null;
        for (var k in window) {
            try {
                if (window[k] instanceof L.Map) { mapObj = window[k]; break; }
            } catch(e) {}
        }
        if (!mapObj) { setTimeout(setup, 100); return; }
        mapObj.on('baselayerchange', function(e) {
            showLegend(e.name);
        });
    }
    document.addEventListener('DOMContentLoaded', setup);
})();
</script>
"""
