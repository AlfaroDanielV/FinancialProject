#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
export PYTHONDONTWRITEBYTECODE=1

echo "== Phase 7b mobile typecheck =="
(
  cd mobile
  if [ ! -d node_modules ]; then
    echo "mobile/node_modules missing — run 'cd mobile && npm install' first" >&2
    exit 1
  fi
  npm run typecheck
)

echo "== Phase 7b focused tests =="
uv run pytest -p no:cacheprovider -q \
  tests/test_phase_7b_chat_transfer.py \
  tests/test_phase_7b_transfers_api.py \
  tests/test_phase_7b_account_hard_delete.py \
  tests/test_phase_7b_card_terms.py \
  tests/test_phase_7b_card_parse_document.py \
  tests/test_phase_7b_chat_create_card.py \
  tests/test_phase_7b_revolving_engine.py \
  tests/test_phase_7b_card_analysis_api.py \
  tests/test_tool_card_analysis.py \
  tests/test_phase_7b_card_obligation.py

echo "== Phase 7b regression slice (cashflow byte-lock + envelopes + chat) =="
uv run pytest -p no:cacheprovider -q \
  tests/test_telegram_dispatcher.py \
  tests/test_llm_extractor.py \
  tests/test_envelopes.py \
  tests/test_envelope_reservations.py \
  tests/test_fixed_expense_attachment.py \
  tests/test_phase7_monthly_cashflow.py \
  tests/test_phase7_unified_cashflow_regression.py \
  tests/test_phase7_per_item_gate.py \
  tests/test_phase7_attach_suggestion.py \
  tests/test_phase7_debt_projection.py \
  tests/test_affordability.py \
  tests/test_phase_7a_subenvelopes.py \
  tests/test_phase_7a_context.py \
  tests/test_tool_registry.py \
  tests/test_system_prompt_builder.py \
  tests/test_phase_6c_b9_system_prompt.py \
  tests/test_phase_6f_chat_create_debt.py \
  tests/test_phase_6f_chat_assign_envelope.py

echo "== Phase 7b checks passed =="
