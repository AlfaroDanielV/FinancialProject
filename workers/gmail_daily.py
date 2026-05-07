"""Standalone daily Gmail scan worker.

Executed in production as an Azure Container Apps Job triggered by
cron `0 9 * * * UTC` (3am Costa Rica). Run manually for testing with:

    uv run python -m workers.gmail_daily

The worker iterates over every active gmail_credentials row and:
    1. Computes `since` from the last successful run (or 2 days ago).
    2. Calls scan_user_inbox with mode='daily'.
    3. Lets the notifier handle finish messages (it'll batch / accumulate
       shadow IDs / send "esta semana" rolls per the addenda decisions).
    4. Calls maybe_send_shadow_summary so users in shadow window receive
       yesterday's roll-up.

Exceptions per-user are caught and logged so one bad user doesn't kill
the run for everyone else. The exit code is always 0 unless setup
fails (DB connection, etc.) — the orchestrator only retries on infra
errors, not per-user failures.

Dependencies on the bot package are intentionally minimal — the
notifier handles Telegram delivery via best-effort `bot.app.get_bot`,
so this worker doesn't have to start the bot itself.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import AsyncSessionLocal
from api.logging_config import setup_logging
from api.models.gmail_credential import GmailCredential
from api.models.gmail_ingestion_run import GmailIngestionRun
from api.services.gmail import notifier as notifier_mod
from api.services.gmail.scanner import scan_user_inbox


log = logging.getLogger("workers.gmail_daily")


# Fallback window when there's no prior successful run (or it's older
# than this). 2 days = enough overlap that one missed cron doesn't lose
# emails.
_FALLBACK_WINDOW_DAYS = 2


async def _last_successful_since(
    *, db: AsyncSession, user_id
) -> Optional[datetime]:
    """The started_at of the most recent run that finished. We use
    started_at (not finished_at) so the next run picks up everything
    from when the previous scan started — slightly conservative, but
    the gmail_messages_seen dedup guarantees no double-processing."""
    row = await db.execute(
        select(GmailIngestionRun.started_at)
        .where(GmailIngestionRun.user_id == user_id)
        .where(GmailIngestionRun.finished_at.is_not(None))
        .order_by(GmailIngestionRun.started_at.desc())
        .limit(1)
    )
    return row.scalar_one_or_none()


async def _scan_one_user(*, user_id) -> None:
    """Execute the daily scan for a single user. All exceptions are
    logged, never raised — the worker keeps going."""
    try:
        async with AsyncSessionLocal() as db:
            last_started = await _last_successful_since(
                db=db, user_id=user_id
            )
            if last_started is None:
                since = datetime.now(timezone.utc) - timedelta(
                    days=_FALLBACK_WINDOW_DAYS
                )
            else:
                # Ensure tz-aware for the scanner.
                if last_started.tzinfo is None:
                    last_started = last_started.replace(tzinfo=timezone.utc)
                since = last_started

            result = await scan_user_inbox(
                user_id=user_id,
                since=since,
                until=None,
                mode="daily",
                db=db,
            )

        async with AsyncSessionLocal() as db:
            await notifier_mod.notify_run_completed(
                user_id=user_id, result=result, db=db
            )
            # Shadow summary: read yesterday's accumulator (regardless of
            # whether this scan added anything to today). Idempotent —
            # the helper clears the key after a successful send.
            await notifier_mod.maybe_send_shadow_summary(
                user_id=user_id, db=db
            )

        log.info(
            "daily_done user=%s scanned=%d created=%d matched=%d skipped=%d",
            user_id,
            result.messages_scanned,
            result.transactions_created,
            result.transactions_matched,
            result.transactions_skipped,
        )
    except Exception:
        log.exception("daily_scan_error user=%s", user_id)


async def run_daily_for_all_users() -> None:
    """Iterate every active credential and run a daily scan. Returns a
    dict of (user_id → outcome string) for callers that want details."""
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(GmailCredential.user_id)
            .where(GmailCredential.activated_at.is_not(None))
            .where(GmailCredential.revoked_at.is_(None))
        )
        user_ids = [r[0] for r in rows.fetchall()]

    log.info("daily_run_started users=%d", len(user_ids))

    for user_id in user_ids:
        await _scan_one_user(user_id=user_id)

    log.info("daily_run_completed users=%d", len(user_ids))


async def main() -> None:
    setup_logging("INFO")
    await run_daily_for_all_users()


if __name__ == "__main__":
    asyncio.run(main())
