#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
export PYTHONDONTWRITEBYTECODE=1

echo "== Phase 7d mobile typecheck =="
(
  cd mobile
  if [ ! -d node_modules ]; then
    echo "mobile/node_modules missing — run 'cd mobile && npm install' first" >&2
    exit 1
  fi
  npm run typecheck
)

echo "== Phase 7d focused tests =="
uv run pytest -p no:cacheprovider -q \
  tests/test_phase_7d_goal_funding.py \
  tests/test_phase_6e_b9_goals.py \
  tests/test_phase_6f_b13_goals.py \
  tests/test_phase_6e_b2_backend.py

echo "== Phase 7d regression slice (analytics exclusions + cashflow byte-lock) =="
uv run pytest -p no:cacheprovider -q \
  tests/test_tool_aggregate_transactions.py \
  tests/test_tool_list_transactions.py \
  tests/test_tool_compare_periods.py \
  tests/test_phase_6e_b5_transactions.py \
  tests/test_phase_6e_b4_accounts.py \
  tests/test_envelopes.py \
  tests/test_envelope_reservations.py \
  tests/test_phase7_monthly_cashflow.py \
  tests/test_phase7_unified_cashflow_regression.py \
  tests/test_phase7_per_item_gate.py \
  tests/test_phase7_goal_feasibility.py \
  tests/test_phase_7a_goal_advisor.py \
  tests/test_phase_6f_chat_create_goal.py \
  tests/test_phase_7b_account_hard_delete.py \
  tests/test_phase_7b_chat_transfer.py \
  tests/test_phase_7b_card_obligation.py \
  tests/test_telegram_dispatcher.py

echo "== Phase 7d checks passed =="
