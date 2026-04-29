"""Phase 6a — bloque 8.5: daily LLM token budget enforcement.

Source of truth is the DB. We sum the tokens already spent today (in
the user's local calendar) across BOTH dispatchers:

- `llm_extractions` (Phase 5b extractor — write dispatcher entry)
- `llm_query_dispatches` (Phase 6a query dispatcher — multi-iter loop)

Cache_read tokens are NOT counted: they're billed at ~10% of fresh
tokens by Anthropic, and penalizing a user for re-using cached
prefixes would defeat the point of conversation history. See
docs/phase-6a-decisions.md (entry 2026-04-29).

The check is invoked BEFORE the LLM call; if the projected total
crosses the cap, we raise `BudgetExceeded` and the dispatcher's
delivery layer maps it to the Spanish user message.

Reset is at midnight in `users.timezone` (default America/Costa_Rica).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.models.llm_extraction import LLMExtraction
from api.models.llm_query_dispatch import LLMQueryDispatch
from app.queries.delivery import BudgetExceeded

log = logging.getLogger("api.services.budget")

# Buffer reserved for the current LLM call we're about to make. The
# value is the typical cost of a query dispatch round-trip on Sonnet
# 4.5 (uncached): ~1500 input + ~500 output. Conservative — better to
# refuse the 100,001st token than the 99,500th.
DEFAULT_QUERY_COST_BUFFER = 2000


def _midnight_utc(tz_name: str) -> datetime:
    """Return the most recent local-midnight as a UTC-aware datetime.

    `created_at` columns are TIMESTAMPTZ; comparing against a UTC-aware
    datetime works cleanly. If `tz_name` is bogus we fall back to UTC
    (matches `_today_for` in bot/pipeline.py).
    """
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # pragma: no cover - defensive
        tz = ZoneInfo("UTC")
    now_local = datetime.now(tz)
    midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_local.astimezone(timezone.utc)


async def current_daily_spend(
    *,
    user_id: uuid.UUID,
    db: AsyncSession,
    tz_name: str = "America/Costa_Rica",
) -> int:
    """Sum input+output tokens spent by `user_id` since local midnight.

    Excludes `cache_read_*` columns by construction — we never read them.
    Returns 0 for a user with no activity today.
    """
    cutoff = _midnight_utc(tz_name)

    ext_q = select(
        func.coalesce(func.sum(LLMExtraction.input_tokens), 0)
        + func.coalesce(func.sum(LLMExtraction.output_tokens), 0)
    ).where(
        LLMExtraction.user_id == user_id,
        LLMExtraction.created_at >= cutoff,
    )
    qd_q = select(
        func.coalesce(func.sum(LLMQueryDispatch.total_input_tokens), 0)
        + func.coalesce(func.sum(LLMQueryDispatch.total_output_tokens), 0)
    ).where(
        LLMQueryDispatch.user_id == user_id,
        LLMQueryDispatch.created_at >= cutoff,
    )

    ext_total = (await db.execute(ext_q)).scalar() or 0
    qd_total = (await db.execute(qd_q)).scalar() or 0
    return int(ext_total) + int(qd_total)


async def assert_within_budget(
    *,
    user_id: uuid.UUID,
    db: AsyncSession,
    tz_name: str = "America/Costa_Rica",
    buffer_tokens: int = DEFAULT_QUERY_COST_BUFFER,
) -> int:
    """Raise BudgetExceeded if `spent + buffer >= cap`. Returns spent.

    `buffer_tokens` reserves headroom for the call about to happen so
    we reject BEFORE making it (the spec's "input + ~500 output buffer"
    requirement). Setting it to 0 disables the headroom, which is
    useful for tests that exercise the boundary.

    `cap <= 0` disables the gate entirely (used in tests that don't
    care about budgets).
    """
    cap = settings.llm_daily_token_budget_per_user
    if cap <= 0:
        return 0
    spent = await current_daily_spend(user_id=user_id, db=db, tz_name=tz_name)
    if spent + buffer_tokens >= cap:
        log.info(
            "budget_exceeded user_id=%s spent=%d cap=%d buffer=%d",
            user_id,
            spent,
            cap,
            buffer_tokens,
        )
        raise BudgetExceeded(
            f"daily budget exceeded: spent={spent} cap={cap} buffer={buffer_tokens}"
        )
    return spent
