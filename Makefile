VENV   := .venv
PYTHON := $(VENV)/bin/python

.PHONY: setup update run lint format clean

## Create venv and install all dependencies
setup:
	uv sync

## Upgrade all dependencies to latest compatible versions
update:
	uv sync --upgrade

## Generate the heatmap HTML
run:
	uv run python main.py

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
