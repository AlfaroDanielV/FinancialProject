#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
export PYTHONDONTWRITEBYTECODE=1

echo "== Phase 7e focused tests (advice trace + snapshots + consent) =="
uv run pytest -p no:cacheprovider -q \
  tests/test_phase_7e_data_foundation.py

echo "== Phase 7e regression slice (wired surfaces) =="
uv run pytest -p no:cacheprovider -q \
  tests/test_affordability.py \
  tests/test_nudges_actions.py \
  tests/test_nudges_callback.py \
  tests/test_nudges_delivery.py \
  tests/test_nudges_evaluators.py \
  tests/test_nudges_feed.py \
  tests/test_phase_6c_b10_nightly.py \
  tests/test_phase_6f_chat_create_goal.py \
  tests/test_phase_7a_goal_advisor.py \
  tests/test_phase7_goal_feasibility.py \
  tests/test_phase7_over_commitment_nudge.py \
  tests/test_envelopes.py \
  tests/test_envelope_reservations.py \
  tests/test_phase7_monthly_cashflow.py \
  tests/test_phase7_unified_cashflow_regression.py

echo "== Phase 7e checks passed =="
