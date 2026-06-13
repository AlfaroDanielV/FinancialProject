#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
export PYTHONDONTWRITEBYTECODE=1

echo "== Phase 7h mobile typecheck =="
(
  cd mobile
  if [ ! -d node_modules ]; then
    echo "mobile/node_modules missing — run 'cd mobile && npm install' first" >&2
    exit 1
  fi
  npm run typecheck
)

echo "== Phase 7h focused tests (savings excluded from available) =="
uv run pytest -p no:cacheprovider -q \
  tests/test_phase_7h_savings_balance.py

echo "== Phase 7h regression slice (dashboard + cashflow byte-lock + envelopes) =="
uv run pytest -p no:cacheprovider -q \
  tests/test_phase_6e_b2_backend.py \
  tests/test_phase7_unified_cashflow_regression.py \
  tests/test_phase7_monthly_cashflow.py \
  tests/test_envelopes.py \
  tests/test_phase_7d_goal_funding.py

echo "== Phase 7h checks passed =="
