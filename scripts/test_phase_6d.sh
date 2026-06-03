#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
export PYTHONDONTWRITEBYTECODE=1

echo "== Phase 6d backend and bot regression =="
# SPA lint/build and the browser/cookie B11 E2E were removed when the SPA was
# retired at Phase 6f B16. Backend + bot 6d coverage remains.
uv run pytest -p no:cacheprovider -q \
  tests/test_phase_6d_b2_endpoints.py \
  tests/test_phase_6d_b3_magic_link.py \
  tests/test_phase_6d_b6_debts.py \
  tests/test_phase_6d_b7_recurring_bills.py \
  tests/test_phase_6d_b8_lazy_detection.py \
  tests/test_phase_6d_b9_account_creation.py \
  tests/test_phase_6d_b10_welcome.py

echo "== Phase 6d checks passed =="
