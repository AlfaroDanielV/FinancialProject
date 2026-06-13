#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
export PYTHONDONTWRITEBYTECODE=1

echo "== Phase 7f mobile typecheck =="
(
  cd mobile
  if [ ! -d node_modules ]; then
    echo "mobile/node_modules missing — run 'cd mobile && npm install' first" >&2
    exit 1
  fi
  npm run typecheck
)

echo "== Phase 7f focused tests (account buttons) =="
uv run pytest -p no:cacheprovider -q \
  tests/test_phase_7f_account_buttons.py

echo "== Phase 7f regression slice (dispatcher + clarification + chat write) =="
uv run pytest -p no:cacheprovider -q \
  tests/test_telegram_dispatcher.py \
  tests/test_phase_6f_b5_chat_write.py \
  tests/test_phase_7b_chat_transfer.py \
  tests/test_phase_6d_b8_lazy_detection.py \
  tests/test_phase_6d_b9_account_creation.py \
  tests/test_phase_6f_chat_create_goal.py \
  tests/test_phase_6f_chat_create_income.py \
  tests/test_phase_6f_chat_create_bill.py \
  tests/test_phase_6f_chat_create_debt.py

echo "== Phase 7f checks passed =="
