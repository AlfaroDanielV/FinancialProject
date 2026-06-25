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

Per-user exceptions are caught and logged so one bad user doesn't kill
the run for everyone else (those count toward a failed tally but keep
the loop going). The job exits NON-ZERO — so the orchestrator retries
and the run shows Failed — when something systemic is detected:
    * a `_is_systemic` failure (import / secret-store / config) on any
      user re-raises immediately (it will hit every user identically), or
    * every eligible user failed (`_raise_if_all_failed`) — a
      finished-but-empty run is a red flag, not N coincidences.
A run with zero eligible users exits 0 (nothing to do).

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
from api.redis_client import close_redis
from api.services.gmail import notifier as notifier_mod
from api.services.gmail.scanner import scan_user_inbox
from api.services.secrets import SecretStoreUnavailable, close_secret_store


log = logging.getLogger("workers.gmail_daily")


# Fallback window when there's no prior successful run (or it's older
# than this). 2 days = enough overlap that one missed cron doesn't lose
# emails.
_FALLBACK_WINDOW_DAYS = 2


# Failure classes that are NOT user-specific: they recur identically for
# every user, so swallowing them per-user lets the job report Succeeded
# (exit 0) while scanning nobody. Bit us 2026-06-25: the worker image
# shipped without `--extra azure`, so building the azure_kv secret store
# raised SecretStoreUnavailable (cause: ModuleNotFoundError) for every
# user — caught per-user, job stayed green, inboxes never scanned. These
# fail the whole run loudly (non-zero exit → orchestrator retries + a
# visible Failed status) instead.
_SYSTEMIC_EXC = (ImportError, SecretStoreUnavailable)


def _is_systemic(exc: BaseException) -> bool:
    """True if `exc` (or any exception in its cause/context chain) is a
    process-wide failure that will hit every user. We walk the chain
    because the real failure arrives wrapped — SecretStoreUnavailable
    raised `from ModuleNotFoundError`."""
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        if isinstance(cur, _SYSTEMIC_EXC):
            return True
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return False


def _raise_if_all_failed(*, ok: int, failed: int) -> None:
    """A finished-but-entirely-failed run is a red flag — almost always
    systemic (DB/KV/config), not N independent user problems. Raise so
    the job exits non-zero instead of silently reporting success while
    having scanned nobody. A run with no eligible users (ok+failed == 0)
    is fine — there's simply nothing to do."""
    total = ok + failed
    if total > 0 and ok == 0:
        raise RuntimeError(
            f"daily Gmail run failed for all {total} user(s) — treating as "
            f"systemic and failing the job"
        )


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


async def _scan_one_user(*, user_id) -> bool:
    """Execute the daily scan for a single user. Returns True on success,
    False on a swallowed per-user failure (one bad user must not kill the
    run for everyone else). **Systemic** failures (`_is_systemic` — import
    / secret-store / config) are RE-RAISED so the whole job fails loudly
    rather than silently swallowing a problem that hits every user."""
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
        return True
    except Exception as exc:
        if _is_systemic(exc):
            # Not this user's fault — it will recur for everyone. Don't
            # bury it as a per-user error; let it abort the run loudly.
            log.error(
                "daily_scan_systemic_error user=%s — aborting run", user_id
            )
            raise
        log.exception("daily_scan_error user=%s", user_id)
        return False


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

    ok = 0
    failed = 0
    for user_id in user_ids:
        # A systemic failure re-raises here and aborts the run (the
        # remaining users would fail identically). Per-user failures are
        # swallowed (return False) and only count toward the tally.
        if await _scan_one_user(user_id=user_id):
            ok += 1
        else:
            failed += 1

    log.info(
        "daily_run_completed users=%d ok=%d failed=%d",
        len(user_ids),
        ok,
        failed,
    )
    _raise_if_all_failed(ok=ok, failed=failed)


async def main() -> None:
    setup_logging("INFO")
    # Initialize a send-only Telegram bot so the notifier can actually reach the
    # user. Imported here (not module-top) to keep the worker's bot-package
    # coupling minimal and dodge import-time cycles. Best-effort: a missing token
    # / disabled mode no-ops and scans still run, just without notifications.
    from bot.app import start_bot_send_only, stop_bot

    await start_bot_send_only()
    try:
        await run_daily_for_all_users()
    finally:
        # One-shot worker: explicitly release the singletons that hold
        # network sessions before asyncio tears down the loop, or their
        # __del__ logs 'Unclosed client session' / 'Event loop is closed'
        # after the scan already succeeded. The long-lived API never does
        # this (it holds them for its lifetime); a job that exits must.
        await stop_bot()
        await close_secret_store()
        await close_redis()


if __name__ == "__main__":
    asyncio.run(main())
