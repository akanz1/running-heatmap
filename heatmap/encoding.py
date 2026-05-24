"""PNG → base64 data-URI encoding for embedding raster layers in HTML."""

from __future__ import annotations

import base64
from io import BytesIO
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

if TYPE_CHECKING:
    import matplotlib.colors as mcolors


def to_data_uri(rgba_u8: np.ndarray) -> str:
    buf = BytesIO()
    Image.fromarray(rgba_u8, mode="RGBA").save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def colormap_to_uri(norm: np.ndarray, cmap: mcolors.LinearSegmentedColormap) -> str:
    return to_data_uri((cmap(norm) * 255).clip(0, 255).astype(np.uint8))


def rgba_with_alpha_uri(rgb_norm: np.ndarray, alpha_norm: np.ndarray, cmap: mcolors.LinearSegmentedColormap) -> str:
    arr = cmap(rgb_norm).copy()
    arr[:, :, 3] = alpha_norm
    return to_data_uri((arr * 255).clip(0, 255).astype(np.uint8))


def white_alpha_uri(alpha_norm: np.ndarray) -> str:
    h, w = alpha_norm.shape
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[:, :, :3] = 255
    arr[:, :, 3] = (alpha_norm * 255).clip(0, 255).astype(np.uint8)
    return to_data_uri(arr)
