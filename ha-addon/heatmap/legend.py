"""HTML legend assembly for the heatmap output."""

from __future__ import annotations

from datetime import date
from datetime import timedelta
from typing import TYPE_CHECKING

from heatmap.colormaps import CMAP_COUNT
from heatmap.colormaps import CMAP_ELEV
from heatmap.colormaps import CMAP_HILL
from heatmap.colormaps import CMAP_HR
from heatmap.colormaps import CMAP_RECENCY
from heatmap.colormaps import CMAP_SPEED
from heatmap.format import pace_min_per_km

_EPOCH = date(1970, 1, 1)

if TYPE_CHECKING:
    import matplotlib.colors as mcolors


def _cmap_to_css(cmap: mcolors.LinearSegmentedColormap, n: int = 14) -> str:
    stops = []
    for i in range(n):
        t = i / (n - 1)
        r, g, b, a = cmap(t)
        stops.append(f"rgba({int(r * 255)},{int(g * 255)},{int(b * 255)},{a:.2f})")
    return f"linear-gradient(to right, {', '.join(stops)})"


def _row(row_id: str, title: str, grad_css: str, label_lo: str, label_hi: str, visible: bool = False) -> str:  # noqa: FBT001, FBT002, PLR0913
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


def build_legend_html(  # noqa: PLR0913
    speed_range: tuple[float, float],
    hr_range: tuple[float, float],
    grad_range: tuple[float, float],
    count_max: float,
    elev_gain_hi: float,
    date_range_days: tuple[float, float],
    recent_count_3mo_max: float,
    recent_count_max: float,
    recent_count_36mo_max: float,
) -> str:
    s_lo, s_hi = speed_range
    hr_lo, hr_hi = hr_range
    g_lo, g_hi = grad_range
    d_lo, d_hi = date_range_days
    count_max_int = int(count_max)
    recent_count_3mo_max_int = max(1, int(recent_count_3mo_max))
    recent_count_max_int = max(1, int(recent_count_max))
    recent_count_36mo_max_int = max(1, int(recent_count_36mo_max))
    elev_gain_hi_int = max(1, int(elev_gain_hi * 100))  # cm/segment → readable scale

    date_lo_str = (_EPOCH + timedelta(days=int(d_lo))).isoformat() if d_lo > 0 else "—"
    date_hi_str = (_EPOCH + timedelta(days=int(d_hi))).isoformat() if d_hi > 0 else "—"

    freq_css = _cmap_to_css(CMAP_COUNT)
    pace_css = _cmap_to_css(CMAP_SPEED)
    hr_css = _cmap_to_css(CMAP_HR)
    hill_css = _cmap_to_css(CMAP_HILL)
    recency_css = _cmap_to_css(CMAP_RECENCY)

    return f"""
<div id="heatmap-legend" style="
    position:fixed; bottom:28px; right:10px; z-index:9999;
    background:rgba(15,15,15,0.88);
    padding:13px 16px 14px; border-radius:9px;
    color:#ddd; font-family:sans-serif; font-size:12px;
    min-width:210px; line-height:1.4;
    border:1px solid rgba(255,255,255,0.10);
    box-shadow:0 2px 8px rgba(0,0,0,0.6);
">
  {_row("legend-frequency", "Top routes (soft linear)", freq_css, "1 visit", f"{count_max_int} visits")}
  {
        _row(
            "legend-frequency-log",
            "All routes (log)",
            freq_css,
            "1 visit",
            f"{count_max_int}+ visits",
            visible=True,
        )
    }
  {_row("legend-pace-avg", "Pace (average)", pace_css, pace_min_per_km(s_lo), pace_min_per_km(s_hi))}
  {_row("legend-heart-rate-avg", "Heart rate (average)", hr_css, f"{hr_lo:.0f} bpm", f"{hr_hi:.0f} bpm")}
  {
        _row(
            "legend-gradient",
            "Steepness",
            "linear-gradient(to right, rgba(20,140,60,0), rgba(20,140,60,1))",
            f"{g_lo * 100:.1f}%",
            f"{g_hi * 100:.1f}% grade",
        )
    }
  {_row("legend-elev-change", "Up vs down", _cmap_to_css(CMAP_ELEV), "descending", "ascending")}
  {_row("legend-hill", "Hill training (mean ascent / visit)", hill_css, "flat", f"≥{elev_gain_hi_int} cm/seg")}
  {_row("legend-recency", "Recency (most recent visit)", recency_css, date_lo_str, date_hi_str)}
  {_row("legend-freshness-3mo", "Freshness (visits, last 3 mo; 14d boosted)", freq_css, "1", f"{recent_count_3mo_max_int} (log)")}
  {_row("legend-freshness", "Freshness (visits, last 12 mo)", freq_css, "1", f"{recent_count_max_int} (log)")}
  {_row("legend-freshness-36mo", "Freshness (visits, last 36 mo)", freq_css, "1", f"{recent_count_36mo_max_int} (log)")}
</div>
"""
