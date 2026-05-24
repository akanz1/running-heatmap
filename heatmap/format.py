"""Small formatting helpers shared across modules."""
from __future__ import annotations


def pace_min_per_km(speed_ms: float) -> str:
    """Convert speed in m/s to a "M:SS/km" pace string."""
    if speed_ms <= 0:
        return "—"
    secs = 1000 / speed_ms
    return f"{int(secs // 60)}:{int(secs % 60):02d}/km"
