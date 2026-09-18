#!/usr/bin/env bash
# POST all ten public sample cases at a running service and summarise.
#   bash scripts/run_samples.sh http://localhost:8000
set -uo pipefail
BASE="${1:-http://localhost:8000}"
python3 -m harness.judge --base-url "$BASE" --cases tests/data/public_samples.json
