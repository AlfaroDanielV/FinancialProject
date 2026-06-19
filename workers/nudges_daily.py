"""Nudge scheduler worker — runs the Phase 5d evaluate→deliver pipeline.

Fan-out over active users so the proactive-messaging pipeline actually fires
without an external cron. Two callers share `run_nudges_for_all_users`:
  - the in-process scheduler (`api/services/nudges/scheduler.py`) on an interval
  - standalone: `uv run python -m workers.nudges_daily` (prod ACA Job / manual)

Per-user flow (own session each):
  1. evaluate_all  → persist fresh nudge candidates (dedup'd, silenced filtered).
  2. deliver_all   → phrase via Haiku + send via Telegram, subject to the four
     anti-saturation filters (quiet hours / silence / rate limit / dedup).

Per-user exceptions are caught + logged so one bad user doesn't kill the run
(matches `workers/insights_nightly.py`). Idempotent: a re-run with no state
change sends nothing (dedup + rate limit). The exit code is always 0 unless
setup fails — ACA only retries on infra errors.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.config import settings
from api.database import AsyncSessionLocal
from api.logging_config import setup_logging
from api.models.user import User
from api.services.nudges.delivery import deliver_all
from api.services.nudges.orchestrator import evaluate_all
from api.services.nudges.phrasing import (
    AnthropicPhrasingClient,
    NudgePhrasingClient,
)


log = logging.getLogger("workers.nudges_daily")

# Production uses the app's shared pool. Tests inject a NullPool factory bound to
# the per-test event loop (matches the db_with_user fixture) so pooled asyncpg
# connections aren't torn down on a closed loop.
SessionFactory = async_sessionmaker


@dataclass
class NudgeWorkerStats:
    users_scanned: int = 0
    users_succeeded: int = 0
    users_failed: int = 0
    nudges_created: int = 0
    nudges_sent: int = 0
    failed_user_ids: list[str] = field(default_factory=list)


async def _active_user_ids(session_factory: SessionFactory) -> list[uuid.UUID]:
    async with session_factory() as db:
        rows = await db.execute(select(User.id).where(User.status == "active"))
        return [row[0] for row in rows.fetchall()]


async def run_nudges_for_user(
    *,
    user_id: uuid.UUID,
    phrasing_client: NudgePhrasingClient,
    now: datetime,
    session_factory: SessionFactory = AsyncSessionLocal,
) -> Optional[tuple[int, int]]:
    """Evaluate + deliver for one user. Returns (created, sent) or None on
    failure. Own session; never raises — logs and returns None so the caller's
    loop carries on. Telegram send is the production `telegram_send_fn` (a user
    with no telegram_user_id is skipped there, leaving the nudge pending for the
    in-app feed — the Phase 8 native-push boundary)."""
    from bot.nudges_send import telegram_send_fn

    try:
        async with session_factory() as db:
            evaluated = await evaluate_all(db, now=now, user_id=user_id)
            await db.commit()

            delivered = await deliver_all(
                db,
                user_id=user_id,
                phrasing_client=phrasing_client,
                send_fn=telegram_send_fn,
                model=settings.llm_extraction_model,
                now=now,
            )
            await db.commit()

        log.info(
            "nudges_user_done user=%s created=%d sent=%d failed=%d",
            user_id,
            evaluated.created,
            delivered.sent,
            delivered.failed,
        )
        return (evaluated.created, delivered.sent)
    except Exception:
        log.exception("nudges_user_failed user=%s", user_id)
        return None


async def run_nudges_for_all_users(
    *,
    now: Optional[datetime] = None,
    phrasing_client: Optional[NudgePhrasingClient] = None,
    session_factory: SessionFactory = AsyncSessionLocal,
) -> NudgeWorkerStats:
    """Run evaluate→deliver for every active user. Reused by the in-process
    scheduler and the standalone entry point. `phrasing_client` and
    `session_factory` are injectable for tests; production builds an
    AnthropicPhrasingClient once and shares it across users (one cached system
    prompt) and uses the app's shared pool."""
    effective_now = now or datetime.now(timezone.utc)
    client = phrasing_client or AnthropicPhrasingClient(
        api_key=settings.anthropic_api_key
    )

    summary = NudgeWorkerStats()
    user_ids = await _active_user_ids(session_factory)
    summary.users_scanned = len(user_ids)
    log.info("nudges_run_started users=%d", summary.users_scanned)

    for user_id in user_ids:
        result = await run_nudges_for_user(
            user_id=user_id,
            phrasing_client=client,
            now=effective_now,
            session_factory=session_factory,
        )
        if result is None:
            summary.users_failed += 1
            summary.failed_user_ids.append(str(user_id))
            continue
        created, sent = result
        summary.users_succeeded += 1
        summary.nudges_created += created
        summary.nudges_sent += sent

    log.info(
        "nudges_run_done users=%d ok=%d failed=%d created=%d sent=%d",
        summary.users_scanned,
        summary.users_succeeded,
        summary.users_failed,
        summary.nudges_created,
        summary.nudges_sent,
    )
    return summary


async def main() -> None:
    setup_logging("INFO")
    await run_nudges_for_all_users()


if __name__ == "__main__":
    asyncio.run(main())
