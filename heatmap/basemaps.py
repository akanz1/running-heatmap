"""Basemap definitions.

Single source of truth shared by `render.py` (TileLayer creation) and
`layer_panel.py` (panel radios + JS indexer).

Each entry:
  key       — short id, used as the radio value and the JS lookup key
  label     — UI label
  url       — tile URL template; may contain {key} to be filled from env var
  attribution
  max_zoom  — provider cap
  env_var   — required env var (None for keyless providers); if unset, the
              basemap is omitted at runtime
  url_match — substring used by the JS to locate the L.TileLayer instance
              created by Folium (must be present in the rendered URL)
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Basemap:
    key: str
    label: str
    url: str
    attribution: str
    max_zoom: int
    env_var: str | None
    url_match: str


_BASEMAPS: list[Basemap] = [
    Basemap(
        key="stadia_smooth_dark",
        label="Stadia Alidade Smooth Dark",
        # If STADIA_API_KEY is set we send it; otherwise hit the keyless
        # endpoint (Stadia rate-limits unkeyed traffic — sign up for free at
        # https://client.stadiamaps.com/signup/ if you see "account limit").
        url="https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}.png?api_key={key}",
        attribution=(
            "© Stadia Maps © OpenMapTiles © OpenStreetMap contributors"
        ),
        max_zoom=20,
        env_var="STADIA_API_KEY",
        url_match="alidade_smooth_dark",
    ),
    Basemap(
        key="esri_topo",
        label="Esri World Topo",
        url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
        attribution=(
            "Tiles © Esri — Esri, DeLorme, NAVTEQ, TomTom, Intermap, USGS, "
            "and the GIS User Community"
        ),
        max_zoom=19,
        env_var=None,
        url_match="World_Topo_Map",
    ),
    Basemap(
        key="osm",
        label="OSM Mapnik",
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        attribution="© OpenStreetMap contributors",
        max_zoom=19,
        env_var=None,
        url_match="tile.openstreetmap.org",
    ),
    Basemap(
        key="dark",
        label="Dark (DarkMatter)",
        url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
        attribution="© OpenStreetMap contributors © CARTO",
        max_zoom=19,
        env_var=None,
        url_match="basemaps.cartocdn.com/dark_all",
    ),
    Basemap(
        key="stadia_smooth",
        label="Stadia Alidade Smooth",
        url="https://tiles.stadiamaps.com/tiles/alidade_smooth/{z}/{x}/{y}.png?api_key={key}",
        attribution=(
            "© Stadia Maps © OpenMapTiles © OpenStreetMap contributors"
        ),
        max_zoom=20,
        env_var="STADIA_API_KEY",
        url_match="alidade_smooth/",
    ),
]


def available_basemaps() -> list[Basemap]:
    """Return only the basemaps whose env vars (if any) are set."""
    out = []
    for b in _BASEMAPS:
        if b.env_var and not os.environ.get(b.env_var):
            continue
        out.append(b)
    return out


def resolved_url(b: Basemap) -> str:
    """Tile URL with the env-var key substituted, if applicable."""
    if not b.env_var:
        return b.url
    return b.url.replace("{key}", os.environ.get(b.env_var, ""))
