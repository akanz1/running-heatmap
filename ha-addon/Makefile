VENV   := .venv
PYTHON := $(VENV)/bin/python

.PHONY: setup update sync run run-html-only serve admin lint format clean

## Create venv and install all dependencies
setup:
	uv sync

## Upgrade all dependencies to latest compatible versions
update:
	uv sync --upgrade

## Sync new intervals.icu activities into cache/intervals_icu/.
## No-op if INTERVALS_ICU_API_KEY is unset.
sync:
	uv run python -c "from dotenv import load_dotenv; load_dotenv(); from heatmap import configure_logging, sync_intervals_icu; from main import config; configure_logging(); sync_intervals_icu(config)"

## Generate the heatmap HTML + tile pyramid
run:
	uv run python main.py

## Re-render outputs/heatmap.html using the existing tile pyramid (~1 s).
## Use this when iterating on render.py / legend.py / assets.py without
## changing data or pyramid settings. Falls back gracefully if no tiles exist.
run-html-only:
	HEATMAP_HTML_ONLY=1 uv run python main.py

## Serve outputs/ on http://localhost:8000 (needed because TileLayers
## are loose PNGs on disk — browsers block fetch:// for security).
## Returns a transparent PNG for missing tiles (sparse pyramid),
## and silences BrokenPipeError tracebacks on cancelled requests.
serve:
	uv run python -m heatmap.serve

## Start the activity admin UI on http://localhost:8001
## Click exclude / re-import; run `make run` afterwards to rebuild.
admin:
	uv run python -m heatmap.admin_server

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
