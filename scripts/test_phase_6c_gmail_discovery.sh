#!/usr/bin/env bash
# Focused smoke for the Gmail onboarding discovery addendum.
#
# This is intentionally not the existing docs/phase-6c user-memory walking
# tour. The operator prompt called this work "Phase 6c", but in this checkout
# Gmail ingestion is Phase 6b and user memory owns docs/phase-6c.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
export PYTHONDONTWRITEBYTECODE=1

uv run pytest -p no:cacheprovider -q \
  tests/test_bank_directory_cr.py \
  tests/test_gmail_bank_selection_render.py \
  tests/test_gmail_listener.py \
  tests/test_gmail_onboarding.py \
  tests/test_gmail_discovery.py \
  tests/test_gmail_whitelist.py

