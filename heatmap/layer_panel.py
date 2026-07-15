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
    ("Metrics", "Pace", "speed", "legend-pace-avg", False),
    ("Metrics", "Heart rate", "hr", "legend-heart-rate-avg", False),
    ("Metrics", "Steepness", "grad", "legend-gradient", False),
    ("Metrics", "Uphill / downhill", "elev", "legend-elev-change", False),
]

# (canonical activity type, display label, route colour)
ROUTE_TYPES = [
    ("Run", "Runs", "#22d3ee"),
    ("Trail Run", "Trail runs", "#4ade80"),
    ("Hike", "Hikes", "#fb923c"),
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
  #layer-panel input[type=radio],
  #layer-panel input[type=checkbox] { accent-color: #eee; cursor: pointer; }
  #layer-panel .mode-toggle {
    display: grid; grid-template-columns: 1fr 1fr; gap: 3px;
    padding: 3px; border-radius: 7px;
    background: rgba(255,255,255,0.07);
  }
  #layer-panel .mode-toggle label { margin: 0; }
  #layer-panel .mode-toggle input { position: absolute; opacity: 0; pointer-events: none; }
  #layer-panel .mode-toggle span {
    display: block; width: 100%; padding: 4px 7px; border-radius: 5px;
    text-align: center; color: #aaa; box-sizing: border-box;
  }
  #layer-panel .mode-toggle input:checked + span {
    background: rgba(255,255,255,0.15); color: #fff;
    box-shadow: 0 1px 3px rgba(0,0,0,0.35);
  }
  #layer-panel .route-swatch {
    width: 9px; height: 9px; border-radius: 50%; flex: 0 0 9px;
    box-shadow: 0 0 5px currentColor;
  }
  #layer-panel .routes-only { display: none; }
  #view-updated-at { color: #888; font-size: 10px; margin-top: 5px; }
  #route-status { color: #888; font-size: 10px; margin-top: 5px; }
  #segment-controls { margin-top: 5px; }
  #segment-controls .segment-actions { display: flex; gap: 5px; }
  #segment-controls button {
    flex: 1; padding: 5px 7px; border: 1px solid rgba(255,255,255,0.16);
    border-radius: 5px; background: rgba(255,255,255,0.08); color: #eee;
    font: 600 11px/1.2 sans-serif; cursor: pointer;
  }
  #segment-controls button:hover { background: rgba(255,255,255,0.14); }
  #segment-controls button:disabled { color: #666; cursor: default; }
  #segment-status { color: #888; font-size: 10px; margin-top: 5px; max-width: 190px; }
  #segment-results[hidden] { display: none !important; }
  #segment-results {
    position: fixed; right: 10px; bottom: 28px; z-index: 9998;
    width: min(680px, calc(100vw - 290px)); max-height: 58vh; overflow: auto;
    background: rgba(15,15,15,0.92); color: #ddd; border-radius: 9px;
    border: 1px solid rgba(255,255,255,0.10); box-shadow: 0 2px 8px rgba(0,0,0,0.6);
    font: 12px/1.4 sans-serif;
  }
  #segment-results .segment-results-head {
    display: flex; justify-content: space-between; position: sticky; top: 0; z-index: 1;
    padding: 9px 11px 7px; background: rgba(15,15,15,0.98);
    border-bottom: 1px solid rgba(255,255,255,0.08);
  }
  #segment-results .segment-results-count { color: #888; }
  #segment-chart { display: block; width: 100%; height: auto; border-bottom: 1px solid rgba(255,255,255,0.08); }
  #segment-chart text { fill: #aaa; font: 10px sans-serif; }
  #segment-chart .chart-title { fill: #ddd; font-weight: 700; }
  #segment-chart .chart-grid { stroke: rgba(255,255,255,0.10); stroke-width: 1; }
  #segment-chart .chart-pb { stroke: #4ade80; stroke-width: 1; stroke-dasharray: 4 3; }
  #segment-chart .chart-trend { fill: none; stroke: #fbbf24; stroke-width: 1.5; }
  #segment-chart .chart-time-point { fill: #22d3ee; cursor: pointer; }
  #segment-chart .chart-distance-point { fill: #fb923c; cursor: pointer; }
  #segment-chart .chart-pb-point { fill: #4ade80; stroke: #fff; stroke-width: 1; }
  #segment-chart [data-chart-record].is-hovered { stroke: #fff; stroke-width: 2.5; }
  #segment-chart [data-chart-record].is-selected { stroke: #fbbf24; stroke-width: 3; }
  #segment-chart-tooltip[hidden] { display: none; }
  #segment-chart-tooltip {
    position: fixed; z-index: 10001; pointer-events: none;
    padding: 7px 9px; border-radius: 6px; background: rgba(15,15,15,0.97);
    color: #eee; border: 1px solid rgba(255,255,255,0.14);
    box-shadow: 0 2px 8px rgba(0,0,0,0.55); white-space: nowrap;
  }
  #segment-chart-tooltip .tooltip-meta { color: #aaa; margin-top: 2px; }
  #segment-results table { width: 100%; border-collapse: collapse; }
  #segment-results th, #segment-results td {
    padding: 6px 9px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.06);
  }
  #segment-results th { position: sticky; top: 35px; background: rgba(20,20,20,0.98); }
  #segment-results th button {
    padding: 0; border: 0; background: none; color: #aaa; cursor: pointer;
    font: 700 10px/1.2 sans-serif; letter-spacing: 0.06em; text-transform: uppercase;
  }
  #segment-results tbody tr { cursor: pointer; }
  #segment-results tbody tr:hover { background: rgba(255,255,255,0.08); }
  #segment-results tbody tr.is-hovered { background: rgba(255,255,255,0.12); }
  #segment-results tbody tr.is-selected {
    background: rgba(251,191,36,0.12); box-shadow: inset 3px 0 #fbbf24;
  }
  #segment-results td:first-child, #segment-results td:last-child { white-space: nowrap; }
  .segment-gate-label.leaflet-tooltip {
    padding: 2px 5px; border: 0; border-radius: 4px; background: rgba(15,15,15,0.9);
    color: #fff; box-shadow: none; font: 700 9px/1 sans-serif;
  }
  .segment-gate-label.leaflet-tooltip:before { display: none; }
  @media (max-width: 760px) {
    #segment-results { left: 10px; right: 10px; width: auto; max-height: 34vh; }
  }
  .route-tooltip.leaflet-tooltip {
    background: rgba(15,15,15,0.94); color: #eee;
    border: 1px solid rgba(255,255,255,0.14); border-radius: 7px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.55); padding: 7px 9px;
  }
  .route-tooltip.leaflet-tooltip:before { display: none; }
  .route-popup .leaflet-popup-content-wrapper,
  .route-popup .leaflet-popup-tip {
    background: rgba(15,15,15,0.96); color: #eee;
  }
  .route-popup .leaflet-popup-content { margin: 11px 13px; }
  .route-detail-title { font-weight: 700; margin-bottom: 3px; }
  .route-detail-meta { color: #bbb; line-height: 1.5; }
  .km-marker { background: transparent; border: 0; pointer-events: none; }
  .km-marker span {
    display: flex; align-items: center; justify-content: center;
    width: 20px; height: 20px; border-radius: 50%; box-sizing: border-box;
    background: rgba(15,15,15,0.92); color: #fff; font: 700 9px/1 sans-serif;
    border: 2px solid currentColor; box-shadow: 0 1px 5px rgba(0,0,0,0.7);
  }
</style>
"""


def _panel_html(profiles: list[str], default_profile: str) -> str:
    rows = [
        '<div class="group-title">View</div>',
        '<div class="mode-toggle">',
        '<label><input type="radio" name="map-mode" value="heatmap"><span>Heatmap</span></label>',
        '<label><input type="radio" name="map-mode" value="routes" checked><span>Routes</span></label>',
        "</div>",
        '<div id="view-updated-at">Updated —</div>',
        '<div class="divider"></div>',
    ]

    # Activity profile (hidden if only one profile, no point cluttering the UI)
    if len(profiles) > 1:
        rows.append('<div class="heatmap-only">')
        rows.append('<div class="group-title">Activity</div>')
        for p in profiles:
            checked = "checked" if p == default_profile else ""
            label = p.replace("_", " ").title()
            rows.append(f'<label><input type="radio" name="profile" value="{p}" {checked}><span>{label}</span></label>')
        rows.append("</div>")
        rows.append('<div class="divider heatmap-only"></div>')

    rows.append('<div class="routes-only">')
    rows.append('<div class="group-title">Activity types</div>')
    for activity_type, label, colour in ROUTE_TYPES:
        rows.append(
            f'<label><input type="checkbox" name="route-type" value="{activity_type}" checked>'
            f'<span class="route-swatch" style="background:{colour};color:{colour}"></span><span>{label}</span></label>'
        )
    rows.append('<div id="route-status">Select Routes to load activities</div>')
    rows.append('<div class="group-title">Segment</div>')
    rows.append('<div id="segment-controls">')
    rows.append('<div class="segment-actions"><button id="segment-draw" type="button">Draw segment</button>')
    rows.append('<button id="segment-clear" type="button" disabled>Clear</button></div>')
    rows.append('<div id="segment-status">Draw start and finish gates</div>')
    rows.append('</div>')
    rows.append("</div>")
    rows.append('<div class="divider routes-only"></div>')

    rows.append('<div class="group-title">Basemap</div>')
    for i, b in enumerate(available_basemaps()):
        checked = "checked" if i == 0 else ""
        rows.append(
            f'<label><input type="radio" name="basemap" value="{b.key}" {checked}><span>{b.label}</span></label>'
        )
    rows.append('<div class="divider"></div>')

    rows.append('<div class="heatmap-only">')
    last_group = None
    for group, display, subdir, _legend_id, visible in LAYERS:
        if group != last_group:
            rows.append(f'<div class="group-title">{group}</div>')
            last_group = group
        checked = "checked" if visible else ""
        rows.append(
            f'<label><input type="radio" name="heatmap-layer" value="{subdir}" {checked}><span>{display}</span></label>'
        )
    rows.append("</div>")
    results = """
<div id="segment-results" class="routes-only" hidden>
  <div class="segment-results-head"><strong>Segment efforts</strong><span class="segment-results-count"></span></div>
  <svg id="segment-chart" viewBox="0 0 680 245" role="img" aria-label="Segment time and distance by attempt date"></svg>
  <div id="segment-chart-tooltip" role="tooltip" hidden></div>
  <table>
    <thead><tr>
      <th><button type="button" data-sort="date">Date</button></th>
      <th><button type="button" data-sort="activity">Activity</button></th>
      <th><button type="button" data-sort="type">Type</button></th>
      <th><button type="button" data-sort="time">Time</button></th>
      <th><button type="button" data-sort="distance">Distance</button></th>
    </tr></thead>
    <tbody></tbody>
  </table>
</div>"""
    return '<div id="layer-panel">\n' + "\n".join(rows) + "\n</div>" + results


def _layer_meta_json() -> str:
    """Subdir → {legendId} so the JS can also swap the legend section."""
    return json.dumps({subdir: {"legend": legend_id} for _, _, subdir, legend_id, _ in LAYERS})


def _basemap_meta_json() -> str:
    """Key → url_match substring so the JS can locate each TileLayer."""
    return json.dumps({b.key: b.url_match for b in available_basemaps()})


def _route_type_meta_json() -> str:
    """Canonical activity type → route colour."""
    return json.dumps({activity_type: {"colour": colour} for activity_type, _, colour in ROUTE_TYPES})


_PANEL_JS_TMPL = """
<script>
(function() {
  var LAYER_META = __LAYER_META__;
  var BASEMAP_META = __BASEMAP_META__;
  var ROUTE_TYPE_META = __ROUTE_TYPE_META__;
  var HEATMAP_UPDATED_AT = __HEATMAP_UPDATED_AT__;

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
    if (!mapObj.getPane("segmentPane")) mapObj.createPane("segmentPane");
    mapObj.getPane("segmentPane").style.zIndex = 625;
    mapObj.getPane("segmentPane").style.pointerEvents = "none";
    var layersByProfile = indexHeatmapLayers();
    var basemaps = indexBasemaps();
    var routeRenderer = L.canvas({padding: 0.5, tolerance: 6});
    var routeGlowGroup = L.layerGroup();
    var routeGroup = L.layerGroup();
    var highlightGroup = L.layerGroup();
    var segmentGroup = L.layerGroup();
    var segmentDraftGroup = L.layerGroup();
    var routeRecords = [];
    var visibleRouteRecords = [];
    var routesPromise = null;
    var routeFiltersInitialized = false;
    var routesUpdatedAt = null;
    var selectedRoute = null;
    var hoveredRoute = null;
    var segment = loadSavedSegment();
    var segmentDraft = [];
    var segmentDrawing = false;
    var segmentSort = {key: "time", direction: 1};
    var heatmapMaxZoom = mapObj.getMaxZoom();
    var routesMaxZoom = heatmapMaxZoom + 1;

    var modeInput = document.querySelector('input[name="map-mode"]:checked');
    var activeMode = modeInput ? modeInput.value : "routes";
    var profileInput = document.querySelector('input[name="profile"]:checked');
    var activeProfile = profileInput ? profileInput.value
                                     : Object.keys(layersByProfile)[0];

    var layerInput = document.querySelector('input[name="heatmap-layer"]:checked');
    var activeLayer = layerInput ? layerInput.value : "count_log";

    function updateLastUpdated() {
      var element = document.getElementById("view-updated-at");
      if (!element) return;
      var value = activeMode === "routes" ? routesUpdatedAt : HEATMAP_UPDATED_AT[activeProfile];
      if (!value) {
        element.textContent = "Updated —";
        element.removeAttribute("title");
        return;
      }
      var updated = new Date(value);
      var ageMinutes = Math.max(0, Math.floor((Date.now() - updated.getTime()) / 60000));
      var age;
      if (ageMinutes < 1) age = "just now";
      else if (ageMinutes < 60) age = ageMinutes + " min ago";
      else if (ageMinutes < 1440) age = Math.floor(ageMinutes / 60) + " h ago";
      else age = Math.floor(ageMinutes / 1440) + " d ago";
      element.textContent = "Updated " + age;
      element.title = updated.toLocaleString();
    }

    function routeStyle(activityType) {
      var zoom = mapObj.getZoom();
      var weight = zoom <= 3 ? 8.0 : zoom <= 5 ? 4.0 : zoom <= 8 ? 2.5 : zoom <= 11 ? 1.7 : zoom <= 14 ? 1.8 : zoom <= 16 ? 2.4 : 3.2;
      var opacity = zoom <= 3 ? 1.0 : zoom <= 5 ? 0.90 : zoom <= 8 ? 0.65 : zoom <= 11 ? 0.38 : zoom <= 14 ? 0.36 : zoom <= 16 ? 0.44 : 0.54;
      return {
        color: ROUTE_TYPE_META[activityType].colour,
        weight: weight,
        opacity: opacity,
        lineCap: "round",
        lineJoin: "round",
        interactive: true,
        bubblingMouseEvents: false,
        renderer: routeRenderer
      };
    }

    function routeGlowStyle(activityType) {
      var zoom = mapObj.getZoom();
      return {
        color: ROUTE_TYPE_META[activityType].colour,
        weight: zoom <= 3 ? 24 : zoom <= 5 ? 14 : zoom <= 8 ? 8 : 1,
        opacity: zoom <= 3 ? 0.28 : zoom <= 5 ? 0.22 : zoom <= 8 ? 0.15 : 0,
        lineCap: "round",
        lineJoin: "round",
        interactive: false,
        renderer: routeRenderer
      };
    }

    function segmentDistanceM(a, b) {
      var earthRadiusM = 6371000;
      var lat1 = a[0] * Math.PI / 180, lat2 = b[0] * Math.PI / 180;
      var dLat = lat2 - lat1, dLon = (b[1] - a[1]) * Math.PI / 180;
      var h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
      return earthRadiusM * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
    }

    function loadSavedSegment() {
      try {
        var saved = JSON.parse(localStorage.getItem("running-heatmap-segment"));
        if (saved && saved.start && saved.finish && saved.start.length === 2 && saved.finish.length === 2) {
          return saved;
        }
      } catch (error) {}
      return null;
    }

    function saveSegment() {
      try { localStorage.setItem("running-heatmap-segment", JSON.stringify(segment)); } catch (error) {}
    }

    function setSegmentStatus(message) {
      var status = document.getElementById("segment-status");
      if (status) status.textContent = message;
    }

    function gateLayer(points, colour, label) {
      return L.polyline(points, {pane: "segmentPane", color: colour, weight: 5, opacity: 0.95, dashArray: "9 5", interactive: false})
        .bindTooltip(label, {permanent: true, direction: "center", className: "segment-gate-label"});
    }

    function renderSegment() {
      segmentGroup.clearLayers();
      if (!segment) return;
      gateLayer(segment.start, "#4ade80", "START").addTo(segmentGroup);
      gateLayer(segment.finish, "#f87171", "FINISH").addTo(segmentGroup);
      if (activeMode === "routes" && !mapObj.hasLayer(segmentGroup)) segmentGroup.addTo(mapObj);
      var clearButton = document.getElementById("segment-clear");
      if (clearButton) clearButton.disabled = false;
    }

    function renderSegmentDraft() {
      segmentDraftGroup.clearLayers();
      segmentDraft.forEach(function(point) {
        L.circleMarker(point, {pane: "segmentPane", radius: 4, color: "#fff", fillOpacity: 1, interactive: false})
          .addTo(segmentDraftGroup);
      });
      if (segmentDraft.length >= 2) gateLayer(segmentDraft.slice(0, 2), "#4ade80", "START").addTo(segmentDraftGroup);
      if (segmentDraft.length >= 4) gateLayer(segmentDraft.slice(2, 4), "#f87171", "FINISH").addTo(segmentDraftGroup);
      if (!mapObj.hasLayer(segmentDraftGroup)) segmentDraftGroup.addTo(mapObj);
    }

    function beginSegmentDrawing() {
      segmentDrawing = true;
      segmentDraft = [];
      segmentDraftGroup.clearLayers();
      if (mapObj.hasLayer(segmentGroup)) mapObj.removeLayer(segmentGroup);
      mapObj.getContainer().style.cursor = "crosshair";
      document.getElementById("segment-draw").textContent = "Cancel";
      setSegmentStatus("Start gate: choose first point");
    }

    function addSegmentPoint(latlng) {
      segmentDraft.push([latlng.lat, latlng.lng]);
      renderSegmentDraft();
      var prompts = [
        "Start gate: choose first point",
        "Start gate: choose second point",
        "Finish gate: choose first point",
        "Finish gate: choose second point"
      ];
      if (segmentDraft.length < 4) {
        setSegmentStatus(prompts[segmentDraft.length]);
        return;
      }
      segment = {start: segmentDraft.slice(0, 2), finish: segmentDraft.slice(2, 4)};
      saveSegment();
      segmentDrawing = false;
      segmentDraft = [];
      segmentDraftGroup.clearLayers();
      if (mapObj.hasLayer(segmentDraftGroup)) mapObj.removeLayer(segmentDraftGroup);
      mapObj.getContainer().style.cursor = "";
      document.getElementById("segment-draw").textContent = "Draw segment";
      renderSegment();
      recomputeSegmentEfforts();
    }

    function cancelSegmentDrawing() {
      segmentDrawing = false;
      segmentDraft = [];
      segmentDraftGroup.clearLayers();
      if (mapObj.hasLayer(segmentDraftGroup)) mapObj.removeLayer(segmentDraftGroup);
      mapObj.getContainer().style.cursor = "";
      document.getElementById("segment-draw").textContent = "Draw segment";
      renderSegment();
      updateSegmentResults();
    }

    function clearSegment() {
      cancelSegmentDrawing();
      segment = null;
      segmentGroup.clearLayers();
      if (mapObj.hasLayer(segmentGroup)) mapObj.removeLayer(segmentGroup);
      routeRecords.forEach(function(record) { record.segmentEffort = null; });
      try { localStorage.removeItem("running-heatmap-segment"); } catch (error) {}
      document.getElementById("segment-clear").disabled = true;
      setSegmentStatus("Draw start and finish gates");
      updateSegmentResults();
    }

    function intersectionFraction(a, b, c, d) {
      var rx = b[1] - a[1], ry = b[0] - a[0];
      var sx = d[1] - c[1], sy = d[0] - c[0];
      var denominator = rx * sy - ry * sx;
      if (Math.abs(denominator) < 1e-12) return null;
      var qx = c[1] - a[1], qy = c[0] - a[0];
      var alongRoute = (qx * sy - qy * sx) / denominator;
      var alongGate = (qx * ry - qy * rx) / denominator;
      var epsilon = 1e-9;
      if (alongRoute < -epsilon || alongRoute > 1 + epsilon || alongGate < -epsilon || alongGate > 1 + epsilon) {
        return null;
      }
      return Math.max(0, Math.min(1, alongRoute));
    }

    function cumulativeDistances(activity) {
      if (activity.__cumulativeDistances) return activity.__cumulativeDistances;
      var distances = [0];
      for (var i = 1; i < activity.points.length; i++) {
        distances.push(distances[i - 1] + segmentDistanceM(activity.points[i - 1], activity.points[i]));
      }
      activity.__cumulativeDistances = distances;
      return distances;
    }

    function activityElapsed(activity) {
      if (activity.__segmentElapsed) return activity.__segmentElapsed;
      var supplied = activity.elapsed_s;
      if (supplied && supplied.length === activity.points.length && supplied.some(Number.isFinite)) {
        activity.__segmentElapsed = supplied;
        activity.__segmentTimeApproximate = false;
        return supplied;
      }
      var distances = cumulativeDistances(activity);
      var totalDistance = distances[distances.length - 1];
      var totalTime = activity.moving_time_s;
      activity.__segmentElapsed = distances.map(function(distance) {
        return totalDistance && totalTime != null ? distance / totalDistance * totalTime : null;
      });
      activity.__segmentTimeApproximate = true;
      return activity.__segmentElapsed;
    }

    function activityProgress(activity) {
      if (activity.__segmentProgress) return activity.__segmentProgress;
      var supplied = activity.progress_m;
      if (supplied && supplied.length === activity.points.length && supplied.some(Number.isFinite)) {
        activity.__segmentProgress = supplied;
        activity.__segmentDistanceApproximate = false;
        return supplied;
      }
      activity.__segmentProgress = cumulativeDistances(activity);
      activity.__segmentDistanceApproximate = true;
      return activity.__segmentProgress;
    }

    function interpolatedElapsed(elapsed, index, fraction) {
      var start = elapsed[index - 1], finish = elapsed[index];
      if (!Number.isFinite(start) || !Number.isFinite(finish)) return null;
      return start + (finish - start) * fraction;
    }

    function segmentEffort(activity) {
      if (!segment) return null;
      var elapsed = activityElapsed(activity);
      var pathDistances = activityProgress(activity);
      var started = false, startElapsed = null, startDistance = null, startProgress = null;
      for (var i = 1; i < activity.points.length; i++) {
        var events = [];
        var startFraction = intersectionFraction(activity.points[i - 1], activity.points[i], segment.start[0], segment.start[1]);
        var finishFraction = intersectionFraction(activity.points[i - 1], activity.points[i], segment.finish[0], segment.finish[1]);
        if (startFraction != null) events.push({kind: "start", fraction: startFraction});
        if (finishFraction != null) events.push({kind: "finish", fraction: finishFraction});
        events.sort(function(a, b) { return a.fraction - b.fraction; });
        for (var j = 0; j < events.length; j++) {
          var event = events[j];
          var progress = i - 1 + event.fraction;
          if (!started && event.kind === "start") {
            started = true;
            startProgress = progress;
            startElapsed = interpolatedElapsed(elapsed, i, event.fraction);
            startDistance = interpolatedElapsed(pathDistances, i, event.fraction);
          } else if (started && event.kind === "finish" && progress > startProgress + 1e-9) {
            var finishElapsed = interpolatedElapsed(elapsed, i, event.fraction);
            var finishDistance = interpolatedElapsed(pathDistances, i, event.fraction);
            return {
              seconds: startElapsed == null || finishElapsed == null ? null : finishElapsed - startElapsed,
              approximate: activity.__segmentTimeApproximate,
              distance_m: startDistance == null || finishDistance == null ? null : finishDistance - startDistance,
              distance_approximate: activity.__segmentDistanceApproximate
            };
          }
        }
      }
      return null;
    }

    function recomputeSegmentEfforts() {
      routeRecords.forEach(function(record) { record.segmentEffort = segmentEffort(record.activity); });
      updateSegmentResults();
    }

    function formatSegmentTime(seconds) {
      if (!Number.isFinite(seconds)) return "—";
      var total = Math.max(0, Math.round(seconds));
      var hours = Math.floor(total / 3600);
      var minutes = Math.floor((total % 3600) / 60);
      var secs = String(total % 60).padStart(2, "0");
      return hours ? hours + ":" + String(minutes).padStart(2, "0") + ":" + secs : minutes + ":" + secs;
    }

    function formatSegmentDistance(distanceM) {
      if (!Number.isFinite(distanceM)) return "—";
      return distanceM >= 1000 ? (distanceM / 1000).toFixed(2) + " km" : Math.round(distanceM) + " m";
    }

    function median(values) {
      var sorted = values.slice().sort(function(a, b) { return a - b; });
      var middle = Math.floor(sorted.length / 2);
      return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
    }

    function setEffortClass(record, className) {
      document.querySelectorAll("#segment-chart [data-chart-record], #segment-results tbody tr").forEach(function(element) {
        element.classList.toggle(className, Boolean(record) && element.__routeRecord === record);
      });
    }

    function setEffortHover(record) {
      setEffortClass(record, "is-hovered");
    }

    function setEffortSelected(record) {
      setEffortClass(record, "is-selected");
    }

    function showSegmentTooltip(record, anchor) {
      var tooltip = document.getElementById("segment-chart-tooltip");
      var activity = record.activity, effort = record.segmentEffort;
      var name = activity.label.replace(/^\\d{4}-\\d{2}-\\d{2}\\s+/, "");
      tooltip.innerHTML = '<strong>' + formatDate(activity.date_days) + ' · ' + escapeHtml(name) + '</strong>' +
        '<div class="tooltip-meta">' + escapeHtml(activity.type) + ' · ' + formatSegmentTime(effort.seconds) +
        ' · ' + formatSegmentDistance(effort.distance_m) + '</div>';
      tooltip.hidden = false;
      var anchorBounds = anchor.getBoundingClientRect();
      var tooltipBounds = tooltip.getBoundingClientRect();
      var left = anchorBounds.left + anchorBounds.width / 2 - tooltipBounds.width / 2;
      left = Math.max(8, Math.min(window.innerWidth - tooltipBounds.width - 8, left));
      var top = anchorBounds.top - tooltipBounds.height - 8;
      if (top < 8) top = anchorBounds.bottom + 8;
      tooltip.style.left = left + "px";
      tooltip.style.top = top + "px";
    }

    function hideSegmentTooltip() {
      document.getElementById("segment-chart-tooltip").hidden = true;
    }

    function renderSegmentChart(results) {
      var chart = document.getElementById("segment-chart");
      var chronological = results.slice().sort(function(a, b) {
        return a.record.activity.date_days - b.record.activity.date_days;
      });
      if (!chronological.length) {
        chart.innerHTML = '<text x="340" y="122" text-anchor="middle">No matching efforts</text>';
        return;
      }
      var left = 58, right = 664, plotWidth = right - left;
      var timeTop = 24, distanceTop = 133, plotHeight = 70;
      var dateMin = chronological[0].record.activity.date_days;
      var dateMax = chronological[chronological.length - 1].record.activity.date_days;
      function x(days) { return dateMin === dateMax ? (left + right) / 2 : left + (days - dateMin) / (dateMax - dateMin) * plotWidth; }
      function domain(values) {
        if (!values.length) return [0, 1];
        var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
        var padding = lo === hi ? Math.max(1, Math.abs(lo) * 0.02) : (hi - lo) * 0.08;
        return [lo - padding, hi + padding];
      }
      function y(value, limits, top) {
        return top + (limits[1] - value) / (limits[1] - limits[0]) * plotHeight;
      }
      function grid(limits, top, formatter) {
        var markup = "";
        [limits[1], (limits[0] + limits[1]) / 2, limits[0]].forEach(function(value) {
          var position = y(value, limits, top);
          markup += '<line class="chart-grid" x1="' + left + '" y1="' + position + '" x2="' + right + '" y2="' + position + '"></line>' +
            '<text x="' + (left - 6) + '" y="' + (position + 3) + '" text-anchor="end">' + formatter(value) + '</text>';
        });
        return markup;
      }
      var timeData = chronological.filter(function(result) { return Number.isFinite(result.effort.seconds); });
      var distanceData = chronological.filter(function(result) { return Number.isFinite(result.effort.distance_m); });
      var timeLimits = domain(timeData.map(function(result) { return result.effort.seconds; }));
      var distanceLimits = domain(distanceData.map(function(result) { return result.effort.distance_m; }));
      var personalBest = timeData.length ? Math.min.apply(null, timeData.map(function(result) { return result.effort.seconds; })) : null;
      var trend = timeData.map(function(result, index) {
        return median(timeData.slice(Math.max(0, index - 4), index + 1).map(function(item) { return item.effort.seconds; }));
      });
      var timeAnnotations = timeData.length
        ? '<line class="chart-pb" x1="' + left + '" y1="' + y(personalBest, timeLimits, timeTop) + '" x2="' + right + '" y2="' + y(personalBest, timeLimits, timeTop) + '"></line>' +
          '<text x="' + right + '" y="' + (y(personalBest, timeLimits, timeTop) - 3) + '" text-anchor="end">PB ' + formatSegmentTime(personalBest) + '</text>' +
          '<polyline class="chart-trend" points="' + timeData.map(function(result, index) {
            return x(result.record.activity.date_days) + ',' + y(trend[index], timeLimits, timeTop);
          }).join(" ") + '"></polyline>'
        : '<text x="340" y="64" text-anchor="middle">No timing data</text>';
      var markup = '<title>Segment time and distance by attempt date</title>' +
        '<text class="chart-title" x="' + left + '" y="13">Time · faster ↓</text>' +
        '<text x="' + right + '" y="13" text-anchor="end">dots: attempts · line: 5-attempt median</text>' +
        grid(timeLimits, timeTop, formatSegmentTime) +
        timeAnnotations +
        '<text class="chart-title" x="' + left + '" y="122">Distance · GPS path</text>' +
        grid(distanceLimits, distanceTop, formatSegmentDistance);
      chronological.forEach(function(result, index) {
        var activity = result.record.activity;
        var name = activity.label.replace(/^\\d{4}-\\d{2}-\\d{2}\\s+/, "");
        var tooltip = formatDate(activity.date_days) + " · " + name + " · " + formatSegmentTime(result.effort.seconds) +
          " · " + formatSegmentDistance(result.effort.distance_m);
        if (Number.isFinite(result.effort.seconds)) {
          var pointClass = result.effort.seconds === personalBest ? "chart-time-point chart-pb-point" : "chart-time-point";
          markup += '<circle class="' + pointClass + '" data-chart-record="' + index + '" cx="' + x(activity.date_days) +
            '" cy="' + y(result.effort.seconds, timeLimits, timeTop) + '" r="4" aria-label="' + escapeHtml(tooltip) + '"></circle>';
        }
        if (Number.isFinite(result.effort.distance_m)) {
          markup += '<circle class="chart-distance-point" data-chart-record="' + index + '" cx="' + x(activity.date_days) +
            '" cy="' + y(result.effort.distance_m, distanceLimits, distanceTop) + '" r="3.5" aria-label="' + escapeHtml(tooltip) + '"></circle>';
        }
      });
      markup += '<text x="' + left + '" y="222">' + formatDate(dateMin) + '</text>' +
        '<text x="' + right + '" y="222" text-anchor="end">' + formatDate(dateMax) + '</text>';
      chart.innerHTML = markup;
      chart.querySelectorAll("[data-chart-record]").forEach(function(point) {
        var record = chronological[+point.dataset.chartRecord].record;
        point.__routeRecord = record;
        point.addEventListener("mouseenter", function() {
          hoveredRoute = record;
          setEffortHover(record);
          showSegmentTooltip(record, point);
          showRouteHighlight(record);
        });
        point.addEventListener("mouseleave", function() {
          hoveredRoute = null;
          setEffortHover(null);
          hideSegmentTooltip();
          if (selectedRoute) showRouteHighlight(selectedRoute); else clearRouteHighlight();
        });
        point.addEventListener("click", function() {
          mapObj.closePopup();
          selectRoute(record);
        });
      });
      setEffortSelected(selectedRoute);
    }

    function updateSegmentResults() {
      var panel = document.getElementById("segment-results");
      if (!panel) return;
      if (!segment) {
        panel.hidden = true;
        return;
      }
      var results = visibleRouteRecords
        .filter(function(record) { return record.segmentEffort; })
        .map(function(record) { return {record: record, effort: record.segmentEffort}; });
      renderSegmentChart(results);
      results.sort(function(a, b) {
        var av, bv;
        if (segmentSort.key === "time") { av = a.effort.seconds; bv = b.effort.seconds; }
        else if (segmentSort.key === "distance") { av = a.effort.distance_m; bv = b.effort.distance_m; }
        else if (segmentSort.key === "date") { av = a.record.activity.date_days; bv = b.record.activity.date_days; }
        else if (segmentSort.key === "type") { av = a.record.activity.type; bv = b.record.activity.type; }
        else { av = a.record.activity.label; bv = b.record.activity.label; }
        if (av == null) return bv == null ? 0 : 1;
        if (bv == null) return -1;
        var comparison = typeof av === "string" ? av.localeCompare(bv) : av - bv;
        return comparison * segmentSort.direction;
      });
      panel.querySelector(".segment-results-count").textContent = results.length + " efforts";
      panel.querySelectorAll("th button").forEach(function(button) {
        var labels = {date: "Date", activity: "Activity", type: "Type", time: "Time", distance: "Distance"};
        button.textContent = labels[button.dataset.sort] + (button.dataset.sort === segmentSort.key
          ? (segmentSort.direction > 0 ? " ↑" : " ↓") : "");
      });
      panel.querySelector("tbody").innerHTML = results.map(function(result, index) {
        var activity = result.record.activity;
        var name = activity.label.replace(/^\\d{4}-\\d{2}-\\d{2}\\s+/, "");
        var time = formatSegmentTime(result.effort.seconds);
        if (result.effort.approximate && Number.isFinite(result.effort.seconds)) time = "~" + time;
        var distance = formatSegmentDistance(result.effort.distance_m);
        if (result.effort.distance_approximate && Number.isFinite(result.effort.distance_m)) distance = "~" + distance;
        return '<tr data-segment-row="' + index + '"><td>' + formatDate(activity.date_days) + '</td>' +
          '<td>' + escapeHtml(name) + '</td><td>' + escapeHtml(activity.type) + '</td><td>' + time + '</td><td>' + distance + '</td></tr>';
      }).join("");
      panel.querySelectorAll("tbody tr").forEach(function(row) {
        var record = results[+row.dataset.segmentRow].record;
        row.__routeRecord = record;
        row.addEventListener("mouseenter", function() {
          hoveredRoute = record;
          setEffortHover(record);
          showRouteHighlight(record);
        });
        row.addEventListener("mouseleave", function() {
          hoveredRoute = null;
          setEffortHover(null);
          if (selectedRoute) showRouteHighlight(selectedRoute); else clearRouteHighlight();
        });
        row.addEventListener("click", function() {
          mapObj.closePopup();
          selectRoute(record);
        });
      });
      setEffortSelected(selectedRoute);
      panel.hidden = false;
      setSegmentStatus(results.length + " matching visible activities");
    }

    function kilometerPoints(activity) {
      if (activity.__kilometerPoints) return activity.__kilometerPoints;
      var marks = [], points = activity.points, travelledM = 0, nextM = 1000;
      for (var i = 1; i < points.length; i++) {
        var start = points[i - 1], end = points[i];
        var segmentM = segmentDistanceM(start, end);
        if (!segmentM) continue;
        while (travelledM + segmentM >= nextM) {
          var fraction = (nextM - travelledM) / segmentM;
          marks.push({
            km: nextM / 1000,
            latlng: [start[0] + (end[0] - start[0]) * fraction,
                     start[1] + (end[1] - start[1]) * fraction]
          });
          nextM += 1000;
        }
        travelledM += segmentM;
      }
      activity.__kilometerPoints = marks;
      return marks;
    }

    function clearRouteHighlight() {
      if (mapObj.hasLayer(highlightGroup)) mapObj.removeLayer(highlightGroup);
      highlightGroup.clearLayers();
    }

    function showRouteHighlight(record) {
      clearRouteHighlight();
      if (!record || activeMode !== "routes") return;
      var activity = record.activity;
      var style = routeStyle(activity.type);
      L.polyline(activity.points, {
        color: "#fff", weight: style.weight + 6, opacity: 0.60,
        lineCap: "round", lineJoin: "round", interactive: false
      }).addTo(highlightGroup);
      L.polyline(activity.points, {
        color: style.color, weight: style.weight + 2.5, opacity: 1,
        lineCap: "round", lineJoin: "round", interactive: false
      }).addTo(highlightGroup);

      var markerStepKm = mapObj.getZoom() >= 13 ? 1 : mapObj.getZoom() >= 10 ? 5 : 0;
      if (markerStepKm) {
        kilometerPoints(activity).forEach(function(mark) {
          if (mark.km % markerStepKm) return;
          var icon = L.divIcon({
            className: "km-marker",
            html: '<span style="border-color:' + style.color + '">' + mark.km + '</span>',
            iconSize: [20, 20], iconAnchor: [10, 10]
          });
          L.marker(mark.latlng, {icon: icon, interactive: false, keyboard: false}).addTo(highlightGroup);
        });
      }
      highlightGroup.addTo(mapObj);
    }

    function focusRoute(record) {
      var bounds = record.layer.getBounds();
      if (!bounds.isValid()) return;
      var fitZoom = mapObj.getBoundsZoom(bounds, false, L.point(80, 80));
      var targetZoom = Math.max(mapObj.getMinZoom(), Math.min(routesMaxZoom, fitZoom - 1));
      mapObj.setView(bounds.getCenter(), targetZoom, {animate: true});
    }

    function selectRoute(record) {
      selectedRoute = record;
      setEffortSelected(record);
      updateRouteStyles();
      focusRoute(record);
    }

    function clearRouteSelection() {
      if (!selectedRoute) return;
      selectedRoute = null;
      setEffortSelected(null);
      updateRouteStyles();
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, function(character) {
        return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[character];
      });
    }

    function formatDate(days) {
      return new Date(Date.UTC(1970, 0, 1) + days * 86400000).toISOString().slice(0, 10);
    }

    function formatDuration(seconds) {
      if (!seconds) return "—";
      var totalMinutes = Math.round(seconds / 60);
      var hours = Math.floor(totalMinutes / 60);
      var minutes = totalMinutes % 60;
      return hours ? hours + " h " + minutes + " min" : minutes + " min";
    }

    function formatPace(activity) {
      if (!activity.distance_m || !activity.moving_time_s) return "—";
      var totalSeconds = Math.round(activity.moving_time_s / (activity.distance_m / 1000));
      var minutes = Math.floor(totalSeconds / 60);
      return minutes + ":" + String(totalSeconds % 60).padStart(2, "0") + " /km";
    }

    function activityDetails(activity) {
      var title = activity.label.replace(/^\\d{4}-\\d{2}-\\d{2}\\s+/, "");
      var distance = activity.distance_m ? (activity.distance_m / 1000).toFixed(1) + " km" : "—";
      var elevation = activity.elevation_gain_m == null ? "—" : Math.round(activity.elevation_gain_m) + " m";
      return '<div class="route-detail-title">' + escapeHtml(title) + '</div>' +
        '<div class="route-detail-meta">' + escapeHtml(activity.type) + ' · ' + formatDate(activity.date_days) + '<br>' +
        distance + ' · ' + formatDuration(activity.moving_time_s) + ' · ' + formatPace(activity) +
        '<br>Elevation ' + elevation + '</div>';
    }

    function setRouteStatus(message) {
      var status = document.getElementById("route-status");
      if (status) status.textContent = message;
    }

    function loadRoutes() {
      if (routesPromise) return routesPromise;
      setRouteStatus("Loading activities…");
      routesPromise = fetch("routes.json")
        .then(function(response) {
          if (!response.ok) throw new Error("HTTP " + response.status);
          return response.json();
        })
        .then(function(payload) {
          routesUpdatedAt = payload.generated_at || null;
          updateLastUpdated();
          var loaded = 0;
          payload.activities.forEach(function(activity) {
            if (!ROUTE_TYPE_META[activity.type] || !activity.points || activity.points.length < 2) return;
            var record = {activity: activity, layer: null, glowLayer: null};
            var layer = L.polyline(activity.points, routeStyle(activity.type));
            record.layer = layer;
            record.glowLayer = L.polyline(activity.points, routeGlowStyle(activity.type));
            layer.bindTooltip(activityDetails(activity), {sticky: true, direction: "top", className: "route-tooltip"});
            layer.on("mouseover", function() {
              hoveredRoute = record;
              showRouteHighlight(record);
            });
            layer.on("mouseout", function() {
              hoveredRoute = null;
              if (selectedRoute) showRouteHighlight(selectedRoute);
              else clearRouteHighlight();
            });
            layer.on("click", function(event) {
              if (segmentDrawing) {
                addSegmentPoint(event.latlng);
                return;
              }
              mapObj.closePopup();
              selectRoute(record);
              L.popup({className: "route-popup", maxWidth: 280, autoPan: false})
                .setLatLng(event.latlng)
                .setContent(activityDetails(activity))
                .openOn(mapObj);
            });
            routeRecords.push(record);
            loaded += 1;
          });
          if (segment) recomputeSegmentEfforts();
          setRouteStatus(loaded + " activities loaded");
          return routeRecords;
        })
        .catch(function(error) {
          setRouteStatus("Routes unavailable · run make run");
          throw error;
        });
      return routesPromise;
    }

    function hideRoutes() {
      if (mapObj.hasLayer(routeGlowGroup)) mapObj.removeLayer(routeGlowGroup);
      if (mapObj.hasLayer(routeGroup)) mapObj.removeLayer(routeGroup);
      hoveredRoute = null;
      clearRouteHighlight();
      if (mapObj.hasLayer(segmentGroup)) mapObj.removeLayer(segmentGroup);
      if (segmentDrawing) cancelSegmentDrawing();
    }

    function enabledRouteTypes() {
      var enabled = {};
      document.querySelectorAll('input[name="route-type"]:checked').forEach(function(input) {
        enabled[input.value] = true;
      });
      return enabled;
    }

    function routeStatsActivities() {
      var enabled = enabledRouteTypes();
      return routeRecords
        .filter(function(record) { return enabled[record.activity.type]; })
        .map(function(record) {
          var activity = record.activity;
          return {d: activity.date_days, km: activity.distance_m || 0, s: activity.moving_time_s || 0,
                  el: activity.elevation_gain_m || 0};
        });
    }

    function syncRouteStats(resetRanges) {
      if (window.__statsPanelSetRouteActivities__) {
        window.__statsPanelSetRouteActivities__(routeStatsActivities(), resetRanges);
      }
    }

    function applyRouteFilters() {
      if (activeMode !== "routes") return;
      var enabled = enabledRouteTypes();
      var filters = window.__statsPanelGetFilters__ ? window.__statsPanelGetFilters__() : null;
      routeGlowGroup.clearLayers();
      routeGroup.clearLayers();
      var visible = 0;
      var visibleRecords = [];
      routeRecords.forEach(function(record) {
        var activity = record.activity;
        var distance = activity.distance_m || 0;
        if (!enabled[activity.type]) return;
        if (filters && (activity.date_days < filters.date_min_days || activity.date_days > filters.date_max_days)) return;
        if (filters && (distance < filters.distance_min_m || distance > filters.distance_max_m)) return;
        visibleRecords.push(record);
        visible += 1;
      });
      visibleRecords.forEach(function(record) { routeGlowGroup.addLayer(record.glowLayer); });
      visibleRecords.forEach(function(record) { routeGroup.addLayer(record.layer); });
      visibleRouteRecords = visibleRecords;
      if (!mapObj.hasLayer(routeGlowGroup)) routeGlowGroup.addTo(mapObj);
      if (!mapObj.hasLayer(routeGroup)) routeGroup.addTo(mapObj);
      setRouteStatus(visible + " of " + routeRecords.length + " activities visible");
      if (hoveredRoute && !routeGroup.hasLayer(hoveredRoute.layer)) hoveredRoute = null;
      if (selectedRoute && !routeGroup.hasLayer(selectedRoute.layer)) {
        clearRouteSelection();
        mapObj.closePopup();
      }
      else updateRouteStyles();
      renderSegment();
      updateSegmentResults();
    }

    function showRoutes() {
      applyRouteFilters();
    }

    function updateRouteStyles() {
      routeRecords.forEach(function(record) {
        var glowStyle = routeGlowStyle(record.activity.type);
        var style = routeStyle(record.activity.type);
        if (selectedRoute && record !== selectedRoute) {
          glowStyle.opacity *= 0.2;
          style.opacity *= 0.2;
        }
        record.glowLayer.setStyle(glowStyle);
        record.layer.setStyle(style);
      });
      if (hoveredRoute || selectedRoute) showRouteHighlight(hoveredRoute || selectedRoute);
    }

    function setModeUI() {
      document.querySelectorAll(".heatmap-only").forEach(function(el) {
        el.style.display = activeMode === "heatmap" ? "" : "none";
      });
      document.querySelectorAll(".routes-only").forEach(function(el) {
        el.style.display = activeMode === "routes" ? "block" : "none";
      });
      var legend = document.getElementById("heatmap-legend");
      if (legend) legend.style.display = activeMode === "heatmap" ? "" : "none";
      var stats = document.getElementById("stats-panel");
      if (stats) stats.style.display = "";
      if (activeMode === "heatmap" && mapObj.hasLayer(segmentGroup)) mapObj.removeLayer(segmentGroup);
      else if (activeMode === "routes") renderSegment();
      updateLastUpdated();
    }

    function applyActive() {
      setModeUI();
      if (activeMode === "heatmap") {
        mapObj.setMaxZoom(heatmapMaxZoom);
        if (mapObj.getZoom() > heatmapMaxZoom) mapObj.setZoom(heatmapMaxZoom);
        mapObj.closePopup();
        hideRoutes();
        routeFiltersInitialized = false;
        showHeatmap(mapObj, layersByProfile, activeProfile, activeLayer);
        showLegend(activeLayer);
        if (window.__statsPanelSetProfile__) window.__statsPanelSetProfile__(activeProfile);
      } else {
        mapObj.setMaxZoom(routesMaxZoom);
        hideAllHeatmap(mapObj, layersByProfile);
        showLegend(null);
        loadRoutes().then(function() {
          if (activeMode === "routes") {
            syncRouteStats(!routeFiltersInitialized);
            routeFiltersInitialized = true;
            showRoutes();
          }
        }).catch(function() {});
      }
    }

    renderSegment();
    applyActive();

    document.querySelectorAll('input[name="map-mode"]').forEach(function(input) {
      input.addEventListener('change', function() {
        activeMode = input.value;
        applyActive();
      });
    });

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
    document.querySelectorAll('input[name="route-type"]').forEach(function(input) {
      input.addEventListener('change', function() {
        if (activeMode === "routes") {
          syncRouteStats(false);
          showRoutes();
        }
      });
    });
    document.getElementById("segment-draw").addEventListener("click", function() {
      if (segmentDrawing) cancelSegmentDrawing(); else beginSegmentDrawing();
    });
    document.getElementById("segment-clear").addEventListener("click", clearSegment);
    document.querySelectorAll("#segment-results th button").forEach(function(button) {
      button.addEventListener("click", function() {
        var key = button.dataset.sort;
        if (segmentSort.key === key) segmentSort.direction *= -1;
        else {
          segmentSort.key = key;
          segmentSort.direction = key === "date" ? -1 : 1;
        }
        updateSegmentResults();
      });
    });
    mapObj.on("click", function(event) {
      if (segmentDrawing) addSegmentPoint(event.latlng);
      else {
        mapObj.closePopup();
        clearRouteSelection();
      }
    });
    document.addEventListener("keydown", function(event) {
      if (event.key === "Escape" && segmentDrawing) cancelSegmentDrawing();
    });
    window.addEventListener("activityfilterschange", function() {
      if (activeMode === "routes") applyRouteFilters();
    });
    mapObj.on("zoomend", function() {
      if (activeMode === "routes") updateRouteStyles();
    });
    mapObj.on("popupclose", function() {
      clearRouteSelection();
    });
    setInterval(updateLastUpdated, 60000);

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


def build_layer_panel_html(
    profiles: list[str],
    default_profile: str,
    heatmap_updated_at: dict[str, str] | None = None,
) -> str:
    js = _PANEL_JS_TMPL.replace("__LAYER_META__", _layer_meta_json())
    js = js.replace("__BASEMAP_META__", _basemap_meta_json())
    js = js.replace("__ROUTE_TYPE_META__", _route_type_meta_json())
    js = js.replace("__HEATMAP_UPDATED_AT__", json.dumps(heatmap_updated_at or {}))
    return _PANEL_CSS + _panel_html(profiles, default_profile) + js
