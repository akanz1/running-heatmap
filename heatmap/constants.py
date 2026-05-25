SEMICIRCLE_TO_DEG: float = 180 / 2**31
EARTH_RADIUS_KM: float = 6371.0

# Colormap node lists: [(position, (R, G, B, A)), ...]
# Orange — frequency: dark orange → amber → yellow → cream
CMAP_COUNT_NODES = [
    (0.00, (0.00, 0.00, 0.00, 0.00)),
    (0.01, (0.40, 0.10, 0.00, 0.55)),
    (0.20, (0.99, 0.30, 0.01, 0.80)),
    (0.50, (1.00, 0.65, 0.00, 0.92)),
    (0.80, (1.00, 0.92, 0.20, 0.97)),
    (1.00, (1.00, 1.00, 0.80, 1.00)),
]

# Blue — pace: dark navy → royal blue → periwinkle → near-white blue
CMAP_SPEED_NODES = [
    (0.00, (0.00, 0.10, 0.40, 1.00)),
    (0.35, (0.05, 0.30, 0.80, 1.00)),
    (0.65, (0.20, 0.55, 1.00, 1.00)),
    (0.85, (0.55, 0.75, 1.00, 1.00)),
    (1.00, (0.85, 0.92, 1.00, 1.00)),
]

# Red — heart rate: dark red → #ea4747 → rose → near-white pink
CMAP_HR_NODES = [
    (0.00, (0.40, 0.05, 0.05, 1.00)),
    (0.35, (0.70, 0.12, 0.12, 1.00)),
    (0.65, (0.92, 0.28, 0.28, 1.00)),
    (0.85, (1.00, 0.65, 0.65, 1.00)),
    (1.00, (1.00, 0.90, 0.90, 1.00)),
]

# Hill training: dark navy → purple → red-orange → bright red. Alpha kept
# solid here — the tile renderer drives visibility via a presence-based
# alpha channel, identical to the Gradient (absolute) layer.
CMAP_HILL_NODES = [
    (0.00, (0.10, 0.20, 0.55, 1.00)),  # dark navy
    (0.40, (0.45, 0.15, 0.60, 1.00)),  # purple
    (0.75, (0.90, 0.25, 0.30, 1.00)),  # red-orange
    (1.00, (1.00, 0.10, 0.10, 1.00)),  # bright red
]

# Diverging — gradient change: green (descent) → dark neutral → purple (ascent)
# Green (0.12, 0.80, 0.22) luminance ≈ 0.60; purple (0.82, 0.22, 1.00) ≈ 0.36.
# Pure perceptual balance not achievable without making purple look lavender.
CMAP_ELEV_NODES = [
    (0.00, (0.12, 0.80, 0.22, 1.00)),
    (0.25, (0.06, 0.52, 0.16, 1.00)),
    (0.45, (0.06, 0.20, 0.10, 1.00)),
    (0.50, (0.18, 0.18, 0.18, 1.00)),
    (0.55, (0.22, 0.08, 0.30, 1.00)),
    (0.75, (0.52, 0.06, 0.75, 1.00)),
    (1.00, (0.82, 0.22, 1.00, 1.00)),
]
