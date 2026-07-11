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
    return '<div id="layer-panel">\n' + "\n".join(rows) + "\n</div>"


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
    var layersByProfile = indexHeatmapLayers();
    var basemaps = indexBasemaps();
    var routeRenderer = L.canvas({padding: 0.5, tolerance: 6});
    var routeGlowGroup = L.layerGroup();
    var routeGroup = L.layerGroup();
    var highlightGroup = L.layerGroup();
    var routeRecords = [];
    var routesPromise = null;
    var routeFiltersInitialized = false;
    var routesUpdatedAt = null;
    var selectedRoute = null;
    var hoveredRoute = null;
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
              mapObj.closePopup();
              selectedRoute = record;
              showRouteHighlight(record);
              L.popup({className: "route-popup", maxWidth: 280})
                .setLatLng(event.latlng)
                .setContent(activityDetails(activity))
                .openOn(mapObj);
            });
            routeRecords.push(record);
            loaded += 1;
          });
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
      if (!mapObj.hasLayer(routeGlowGroup)) routeGlowGroup.addTo(mapObj);
      if (!mapObj.hasLayer(routeGroup)) routeGroup.addTo(mapObj);
      setRouteStatus(visible + " of " + routeRecords.length + " activities visible");
      if (hoveredRoute && !routeGroup.hasLayer(hoveredRoute.layer)) hoveredRoute = null;
      if (selectedRoute && !routeGroup.hasLayer(selectedRoute.layer)) mapObj.closePopup();
      else if (hoveredRoute || selectedRoute) showRouteHighlight(hoveredRoute || selectedRoute);
    }

    function showRoutes() {
      applyRouteFilters();
    }

    function updateRouteStyles() {
      routeRecords.forEach(function(record) {
        record.glowLayer.setStyle(routeGlowStyle(record.activity.type));
        record.layer.setStyle(routeStyle(record.activity.type));
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
    window.addEventListener("activityfilterschange", function() {
      if (activeMode === "routes") applyRouteFilters();
    });
    mapObj.on("zoomend", function() {
      if (activeMode === "routes") updateRouteStyles();
    });
    mapObj.on("popupclose", function() {
      if (!selectedRoute) return;
      selectedRoute = null;
      if (hoveredRoute) showRouteHighlight(hoveredRoute);
      else clearRouteHighlight();
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
