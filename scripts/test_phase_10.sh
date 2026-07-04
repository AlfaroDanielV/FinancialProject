#!/usr/bin/env bash
# P10 — Advisory Mode + Principle Library gate.
# Focused suites (B0.5 chat robustness, B1 financial_state, B2/B3 activation +
# persona + tool scoping, B4-B6 assessment tools + the CR pension engine,
# B8 principle library, B9 narration + CI scorers, B10 distress guardrails,
# B11 Option C) + the regression slice covering every seam P10 touched
# (pipeline nets, dispatcher, extractor schema, prompt cache invariants,
# cashflow byte-lock, tool registry).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
export PYTHONDONTWRITEBYTECODE=1

echo "== Phase 10 mobile typecheck =="
(
  cd mobile
  if [ ! -d node_modules ]; then
    echo "mobile/node_modules missing — run 'cd mobile && npm install' first" >&2
    exit 1
  fi
  npm run typecheck
)

echo "== Phase 10 focused tests =="
uv run pytest -p no:cacheprovider -q \
  tests/test_phase_10_b05_chat_robustness.py \
  tests/test_phase_10_b1_financial_state.py \
  tests/test_phase_10_b2_b3_advisory_activation.py \
  tests/test_phase_10_b4_b6_assessment_tools.py \
  tests/test_cr_pension.py \
  tests/test_phase_10_b8_principle_library.py \
  tests/test_phase_10_b9_narration_scorers.py \
  tests/test_phase_10_b10_guardrails.py \
  tests/test_phase_10_b11_option_c.py

echo "== Phase 10 regression slice (pipeline nets + cache invariants + cashflow byte-lock) =="
uv run pytest -p no:cacheprovider -q \
  tests/test_telegram_dispatcher.py \
  tests/test_llm_extractor.py \
  tests/test_query_robustness.py \
  tests/test_phase_6a_routing.py \
  tests/test_dispatcher_with_echo_tool.py \
  tests/test_tool_registry.py \
  tests/test_tool_compare_periods.py \
  tests/test_system_prompt_builder.py \
  tests/test_phase_6c_b9_system_prompt.py \
  tests/test_phase_6f_b5_chat_write.py \
  tests/test_chat_menu_resumen.py \
  tests/test_chat_reclassify.py \
  tests/test_phase7_monthly_cashflow.py \
  tests/test_phase7_unified_cashflow_regression.py \
  tests/test_affordability.py \
  tests/test_envelopes.py \
  tests/test_fixpack_income_tool.py \
  tests/test_phase_8_b4_reallocate.py

echo "== Phase 10 checks passed =="
