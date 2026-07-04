"""Floating stats panel with date + distance sliders.

In-browser only — sliders filter an inline JSON copy of the activities and
update four totals (count, km, hours, m ascent). The heatmap tiles are
pre-baked so the map itself does not change with the sliders.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from heatmap.tracks import Track


@dataclass
class StatsPanelData:
    """Compact, JSON-serialisable input for the stats panel.

    Persisted alongside the tile pyramid so the HEATMAP_HTML_ONLY path can
    rebuild the page without re-parsing tracks.
    """

    activities: list[dict]  # [{d: days, km: meters, s: seconds, el: meters}, ...]
    date_min_days: int
    date_max_days: int
    dist_max_km: int


def stats_panel_data_from_tracks(tracks: list[Track]) -> StatsPanelData:
    activities = []
    date_lo, date_hi = None, None
    dist_max = 0.0
    for t in tracks:
        dist_m = float(t.distance_m or 0.0)
        time_s = float(t.moving_time_s or 0.0)
        elev_m = float(t.elevation_gain_m or 0.0)
        activities.append({"d": t.date_days, "km": dist_m, "s": time_s, "el": elev_m})
        date_lo = t.date_days if date_lo is None else min(date_lo, t.date_days)
        date_hi = t.date_days if date_hi is None else max(date_hi, t.date_days)
        dist_max = max(dist_max, dist_m)

    if date_lo is None:
        date_lo, date_hi = 0, 1
    return StatsPanelData(
        activities=activities,
        date_min_days=date_lo,
        date_max_days=date_hi,
        dist_max_km=max(1, int((dist_max / 1000) + 0.999)),
    )


def _path(output_dir: Path, profile: str) -> Path:
    return output_dir / f"_activities_{profile}.json"


def save_stats_panel_data(data: StatsPanelData, output_dir: Path, profile: str = "all") -> None:
    _path(output_dir, profile).write_text(
        json.dumps(
            {
                "activities": data.activities,
                "date_min_days": data.date_min_days,
                "date_max_days": data.date_max_days,
                "dist_max_km": data.dist_max_km,
            },
            separators=(",", ":"),
        )
    )


def load_stats_panel_data(output_dir: Path, profile: str = "all") -> StatsPanelData | None:
    p = _path(output_dir, profile)
    if not p.exists():
        return None
    raw = json.loads(p.read_text())
    return StatsPanelData(
        activities=raw["activities"],
        date_min_days=raw["date_min_days"],
        date_max_days=raw["date_max_days"],
        dist_max_km=raw["dist_max_km"],
    )


_PANEL_CSS = """
<style>
  #stats-panel {
    position: fixed; bottom: 28px; left: 10px; z-index: 9999;
    background: rgba(15,15,15,0.88);
    padding: 13px 16px 14px; border-radius: 9px;
    color: #ddd; font-family: sans-serif; font-size: 12px;
    min-width: 240px; line-height: 1.4;
    border: 1px solid rgba(255,255,255,0.10);
    box-shadow: 0 2px 8px rgba(0,0,0,0.6);
  }
  #stats-panel h4 {
    margin: 0 0 8px; font-size: 12px; color: #eee;
    text-transform: uppercase; letter-spacing: 0.05em;
  }
  #stats-panel .totals {
    display: grid; grid-template-columns: 1fr 1fr; gap: 4px 12px;
    margin-bottom: 10px;
  }
  #stats-panel .totals .v { color: #eee; font-weight: 600; }
  #stats-panel .totals .k { color: #aaa; font-size: 11px; }
  #stats-panel .slider-block { margin-top: 8px; }
  #stats-panel .slider-block .row { display: flex; justify-content: space-between; color: #aaa; font-size: 11px; }
  #stats-panel .slider-block .row span { color: #eee; }
  #stats-panel .dual-range { position: relative; height: 22px; margin: 4px 0 2px; }
  #stats-panel .dual-range input[type=range] {
    position: absolute; left: 0; right: 0; width: 100%;
    pointer-events: none; appearance: none; background: transparent;
    height: 22px; outline: none;
  }
  #stats-panel .dual-range input[type=range]::-webkit-slider-runnable-track {
    height: 4px; background: rgba(255,255,255,0.15); border-radius: 2px;
  }
  #stats-panel .dual-range input[type=range]::-moz-range-track {
    height: 4px; background: rgba(255,255,255,0.15); border-radius: 2px;
  }
  #stats-panel .dual-range input[type=range]::-webkit-slider-thumb {
    appearance: none; pointer-events: auto;
    width: 14px; height: 14px; border-radius: 50%;
    background: #ddd; border: 1px solid rgba(0,0,0,0.4); cursor: pointer;
    margin-top: -5px;
  }
  #stats-panel .dual-range input[type=range]::-moz-range-thumb {
    pointer-events: auto;
    width: 14px; height: 14px; border-radius: 50%;
    background: #ddd; border: 1px solid rgba(0,0,0,0.4); cursor: pointer;
  }
</style>
"""


def _panel_html(date_min_days: int, date_max_days: int, dist_max_km: int) -> str:
    return f"""
<div id="stats-panel">
  <h4>Filter</h4>
  <div class="totals">
    <div class="k">Activities</div><div class="v" id="stat-count">–</div>
    <div class="k">Distance</div><div class="v" id="stat-dist">–</div>
    <div class="k">Time</div><div class="v" id="stat-time">–</div>
    <div class="k">Elevation</div><div class="v" id="stat-elev">–</div>
  </div>

  <div class="slider-block">
    <div class="row"><span id="date-lo-label">…</span><span id="date-hi-label">…</span></div>
    <div class="dual-range">
      <input type="range" id="date-lo" min="{date_min_days}" max="{date_max_days}" step="1" value="{date_min_days}">
      <input type="range" id="date-hi" min="{date_min_days}" max="{date_max_days}" step="1" value="{date_max_days}">
    </div>
  </div>

  <div class="slider-block">
    <div class="row"><span id="dist-lo-label">…</span><span id="dist-hi-label">…</span></div>
    <div class="dual-range">
      <input type="range" id="dist-lo" min="0" max="{dist_max_km}" step="1" value="0">
      <input type="range" id="dist-hi" min="0" max="{dist_max_km}" step="1" value="{dist_max_km}">
    </div>
  </div>
</div>
"""


_PANEL_JS = """
<script>
(function() {
  var EPOCH = new Date(Date.UTC(1970, 0, 1));
  function fmtDate(days) {
    var d = new Date(EPOCH.getTime() + days * 86400000);
    return d.toISOString().slice(0, 10);
  }
  function fmtKm(m)    { return (m / 1000).toFixed(1) + " km"; }
  function fmtHours(s) {
    var h = s / 3600;
    return h >= 10 ? h.toFixed(0) + " h" : h.toFixed(1) + " h";
  }
  function fmtMeters(m) { return Math.round(m).toLocaleString() + " m"; }

  var activeProfile = window.__DEFAULT_PROFILE__ || "all";

  function dataForProfile(profile) {
    var all = window.__STATS_BY_PROFILE__ || {};
    return all[profile] || { activities: [], date_min_days: 0, date_max_days: 1, dist_max_km: 1 };
  }

  function setup() {
    var dateLo = document.getElementById("date-lo");
    var dateHi = document.getElementById("date-hi");
    var distLo = document.getElementById("dist-lo");
    var distHi = document.getElementById("dist-hi");
    if (!dateLo) { setTimeout(setup, 100); return; }

    function refreshSliderRanges() {
      var d = dataForProfile(activeProfile);
      dateLo.min = d.date_min_days; dateLo.max = d.date_max_days; dateLo.value = d.date_min_days;
      dateHi.min = d.date_min_days; dateHi.max = d.date_max_days; dateHi.value = d.date_max_days;
      distLo.min = 0; distLo.max = d.dist_max_km; distLo.value = 0;
      distHi.min = 0; distHi.max = d.dist_max_km; distHi.value = d.dist_max_km;
    }

    function recompute() {
      var data = dataForProfile(activeProfile).activities;
      // Enforce lo <= hi for both ranges.
      var dl = +dateLo.value, dh = +dateHi.value;
      if (dl > dh) { dateLo.value = dh; dl = dh; }
      var kl = +distLo.value, kh = +distHi.value;
      if (kl > kh) { distLo.value = kh; kl = kh; }

      var minM = kl * 1000, maxM = kh * 1000;
      var count = 0, dist = 0, time = 0, elev = 0;
      for (var i = 0; i < data.length; i++) {
        var a = data[i];
        if (a.d < dl || a.d > dh) continue;
        if (a.km < minM || a.km > maxM) continue;
        count++;
        dist += a.km || 0;
        time += a.s || 0;
        elev += a.el || 0;
      }
      document.getElementById("stat-count").textContent = count;
      document.getElementById("stat-dist").textContent  = fmtKm(dist);
      document.getElementById("stat-time").textContent  = fmtHours(time);
      document.getElementById("stat-elev").textContent  = fmtMeters(elev);
      document.getElementById("date-lo-label").textContent = fmtDate(dl);
      document.getElementById("date-hi-label").textContent = fmtDate(dh);
      document.getElementById("dist-lo-label").textContent = kl + " km";
      document.getElementById("dist-hi-label").textContent = kh + " km";
    }

    [dateLo, dateHi, distLo, distHi].forEach(function(el) {
      el.addEventListener("input", recompute);
    });

    refreshSliderRanges();
    recompute();

    window.__statsPanelSetProfile__ = function(profile) {
      activeProfile = profile;
      refreshSliderRanges();
      recompute();
    };
  }
  document.addEventListener("DOMContentLoaded", setup);
})();
</script>
"""


def _stats_payload(data: StatsPanelData) -> dict:
    return {
        "activities": data.activities,
        "date_min_days": data.date_min_days,
        "date_max_days": data.date_max_days,
        "dist_max_km": data.dist_max_km,
    }


def build_stats_panel_html(
    stats_by_profile: dict[str, StatsPanelData | None],
    default_profile: str,
) -> str:
    """Build the stats panel for inline injection.

    Embeds every profile's activities so JS can swap without re-fetching.
    """
    payload: dict[str, dict] = {}
    for profile, data in stats_by_profile.items():
        if data is None:
            continue
        payload[profile] = _stats_payload(data)

    if not payload:
        return ""

    default = payload.get(default_profile) or next(iter(payload.values()))

    bootstrap_js = (
        "<script>"
        f"window.__STATS_BY_PROFILE__ = {json.dumps(payload, separators=(',', ':'))};"
        f"window.__DEFAULT_PROFILE__ = {json.dumps(default_profile)};"
        "</script>"
    )
    return (
        bootstrap_js
        + _PANEL_CSS
        + _panel_html(default["date_min_days"], default["date_max_days"], default["dist_max_km"])
        + _PANEL_JS
    )
