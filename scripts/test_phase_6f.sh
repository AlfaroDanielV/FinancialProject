#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
export PYTHONDONTWRITEBYTECODE=1

echo "== Phase 6f mobile typecheck =="
(
  cd mobile
  if [ ! -d node_modules ]; then
    echo "mobile/node_modules missing — run 'cd mobile && npm install' first" >&2
    exit 1
  fi
  npm run typecheck
)

echo "== Phase 6f backend focused tests =="
uv run pytest -p no:cacheprovider -q \
  tests/test_phase_6f_b2_chat.py \
  tests/test_phase_6f_b3_device_code.py \
  tests/test_phase_6f_b5_chat_write.py \
  tests/test_phase_6f_b6_vision.py \
  tests/test_phase_6f_b10_bills.py \
  tests/test_phase_6f_b11_debts.py \
  tests/test_phase_6f_b12_incomes.py \
  tests/test_phase_6f_b13_goals.py \
  tests/test_phase_6f_b14_categories.py \
  tests/test_phase_6f_b14_memoria.py \
  tests/test_phase_6f_b15_deep_link.py \
  tests/test_phase_6f_chat_create_goal.py

echo "== Phase 6f backend regression slice (magic-link + onboarding) =="
# Phase 6d auth + onboarding tests guard the SPA cookie path that 6f
# left intact alongside the new bearer-token branch. Re-running them
# here catches any drift introduced by B2's exchange-response change.
uv run pytest -p no:cacheprovider -q \
  tests/test_phase_6d_b2_endpoints.py \
  tests/test_phase_6d_b3_magic_link.py

echo "== Phase 6f checks passed =="
