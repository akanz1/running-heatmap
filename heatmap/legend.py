"""HTML legend assembly for the heatmap output."""

from __future__ import annotations

from typing import TYPE_CHECKING

from heatmap.colormaps import CMAP_COUNT
from heatmap.colormaps import CMAP_ELEV
from heatmap.colormaps import CMAP_HR
from heatmap.colormaps import CMAP_SPEED
from heatmap.format import pace_min_per_km

if TYPE_CHECKING:
    import matplotlib.colors as mcolors

    from heatmap.raster import NormalizedLayers


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


def build_legend_html(layers: NormalizedLayers) -> str:
    s_lo, s_hi = layers.speed_range
    hr_lo, hr_hi = layers.hr_range
    g_lo, g_hi = layers.grad_range
    count_max = int(layers.count_max)

    freq_css = _cmap_to_css(CMAP_COUNT)
    pace_css = _cmap_to_css(CMAP_SPEED)
    hr_css = _cmap_to_css(CMAP_HR)

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
  {_row("legend-frequency", "Frequency (linear)", freq_css, "1 pass", f"{count_max} passes", visible=True)}
  {_row("legend-frequency-log", "Frequency (log)", freq_css, "1 pass", f"{count_max} passes (log scale)")}
  {_row("legend-pace-avg", "Pace (average)", pace_css, pace_min_per_km(s_lo), pace_min_per_km(s_hi))}
  {_row("legend-heart-rate-avg", "Heart rate (average)", hr_css, f"{hr_lo:.0f} bpm", f"{hr_hi:.0f} bpm")}
  {
        _row(
            "legend-gradient",
            "Gradient (absolute)",
            "linear-gradient(to right, rgba(0,0,0,0), rgba(255,255,255,1))",
            f"{g_lo * 100:.1f}%",
            f"{g_hi * 100:.1f}% grade",
        )
    }
  {_row("legend-elev-change", "Gradient (change)", _cmap_to_css(CMAP_ELEV), "descending", "ascending")}
</div>
"""
