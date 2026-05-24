VENV   := .venv
PYTHON := $(VENV)/bin/python

.PHONY: setup update run run-html-only serve lint format clean

## Create venv and install all dependencies
setup:
	uv sync

## Upgrade all dependencies to latest compatible versions
update:
	uv sync --upgrade

## Generate the heatmap HTML + tile pyramid
run:
	uv run python main.py

## Re-render outputs/heatmap.html using the existing tile pyramid (~1 s).
## Use this when iterating on render.py / legend.py / assets.py without
## changing data or pyramid settings. Falls back gracefully if no tiles exist.
run-html-only:
	HEATMAP_HTML_ONLY=1 uv run python main.py

## Serve outputs/ on http://localhost:8000 (needed because TileLayers
## are loose PNGs on disk — browsers block fetch:// for security)
serve:
	cd outputs && uv run python -m http.server 8000

## Check lint + import order (no changes)
lint:
	uv run ruff check .

## Apply ruff fixes (imports + safe lint)
format:
	uv run ruff check --fix .
	uv run ruff format .

## Remove the virtual environment
clean:
	rm -rf $(VENV)
	@echo "Removed $(VENV). Run 'make setup' to recreate."
