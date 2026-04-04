#!/usr/bin/env bash
# scripts/check.sh — Run all local checks: pyright, tests, ruff
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== pyright ==="
.venv/bin/python -m pyright

echo ""
echo "=== pytest ==="
.venv/bin/python -m pytest tests/ -x -q

echo ""
echo "=== ruff ==="
.venv/bin/python -m ruff check verisure_italy/ tests/ custom_components/

echo ""
echo "All checks passed."
