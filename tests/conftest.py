"""Pytest config shared across Phase 5b unit tests.

These tests are deliberately DB-free and LLM-free:
- `FixtureLLMClient` replaces the real Anthropic call.
- `StubAsyncSession` replaces SQLAlchemy so `extract_finance_intent` can
  write a log row without needing a running Postgres.

The Phase 5b smoke script (scripts/phase5b_smoke.sh) is where real DB /
real Telegram integration is exercised. These tests exist to pin the
ExtractionResult schema and dispatcher routing logic, which should never
require a dev environment to run.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
