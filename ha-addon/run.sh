#!/usr/bin/with-contenv bashio

echo "Starting Running Heatmap Add-on..."

# Optional: Set HEATMAP_YES=1 to bypass any prompt when rebuilding
export HEATMAP_YES=1

# Run the generation script
echo "Generating heatmap..."
uv run python main.py

# Start the web server
echo "Starting heatmap server..."
uv run python -m heatmap.serve
