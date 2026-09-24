# Task runner. Recipes mirror the CI gates in .github/workflows/ci.yml.

set shell := ["bash", "-euo", "pipefail", "-c"]

openspec_version := "1.9.0"

# List recipes
default:
    @just --list

# Install dependencies from the lockfile and the notebook output filter
setup:
    uv sync --locked --extra dev
    uv run nbstripout --install

# Lint
lint:
    uv run ruff check src tests scripts

# Apply safe lint fixes
fix:
    uv run ruff check --fix src tests scripts

# Format code
fmt:
    uv run ruff format src tests scripts

# Check formatting without changing files
fmt-check:
    uv run ruff format --check src tests scripts

# Type-check
typecheck:
    uv run mypy

# Run the offline test suite
test *args:
    uv run pytest {{ args }}

# Execute the notebooks (first run queries OpenStreetMap)
notebooks:
    uv run pytest --nbmake --nbmake-timeout=600 notebooks/

# Pair each notebook with its percent-format script
sync-notebooks:
    uv run jupytext --sync notebooks/*.ipynb

# Validate the OpenSpec specs
specs:
    npx --yes @fission-ai/openspec@{{ openspec_version }} validate --all --strict --no-interactive

# Build the study area tiers (step 0)
study-area:
    uv run python scripts/00_build_study_area.py

# Start JupyterLab
lab:
    uv run jupyter lab

# Everything CI runs
ci: check notebooks specs

# Fast pre-push gate: no network
check: lint fmt-check typecheck test
