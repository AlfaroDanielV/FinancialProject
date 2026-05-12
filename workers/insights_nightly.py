"""Phase 6c B10 — nightly user-memory recompute.

Runs in production as an Azure Container Apps Job triggered by cron
`30 9 * * * UTC` (3:30am Costa Rica), 30 min after the Gmail daily
worker so the ledger is current before we recompute insights from it.

Run locally with:

    uv run python -m workers.insights_nightly

Per-user flow (`run_nightly_for_user`):
  1. compute_all over the ledger.
  2. persist_insights(source='computed').
  3. detect_stated_observed_gaps.

After the loop:
  4. expire_stale (global sweep — drops unlocked rows past valid_until,
     redacts raw_quote at age 30d, hard-deletes 30-day-stale rows).

Per-user exceptions are caught and logged so one bad user doesn't kill
the rest of the run. The exit code is always 0 unless setup fails
(DB connection, etc.); ACA only retries on infra errors.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select

from api.database import AsyncSessionLocal
from api.logging_config import setup_logging
from api.models.user import User
from api.services.dashboard.materialized import refresh_dashboard_materialized_views
from api.services.insights.lifecycle import ExpireStats, expire_stale
from api.services.insights.nightly_runner import (
    NightlyRunStats,
    run_nightly_for_user,
)


log = logging.getLogger("workers.insights_nightly")


@dataclass
class NightlyWorkerStats:
    users_scanned: int = 0
    users_succeeded: int = 0
    users_failed: int = 0
    persisted_created: int = 0
    persisted_updated: int = 0
    persisted_reinforced: int = 0
    persisted_skipped_locked: int = 0
    persisted_skipped_user_override: int = 0
    gaps_created: int = 0
    gaps_updated: int = 0
    gaps_reinforced: int = 0
    gaps_expired_unsupported: int = 0
    expired_active: int = 0
    raw_quotes_redacted: int = 0
    hard_deleted: int = 0
    dashboard_materialized_refreshed: bool = False
    failed_user_ids: list[str] = field(default_factory=list)


async def _run_one_user(*, user_id, now: datetime) -> NightlyRunStats | None:
    try:
        async with AsyncSessionLocal() as db:
            stats = await run_nightly_for_user(
                db, user_id=user_id, now=now
            )
            await db.commit()
            log.info(
                "insights_nightly_user_done user=%s proposed=%d created=%d "
                "updated=%d reinforced=%d gaps_emitted=%d",
                user_id,
                stats.insights_proposed,
                stats.persisted_created,
                stats.persisted_updated,
                stats.persisted_reinforced,
                stats.gaps_emitted,
            )
            return stats
    except Exception:
        log.exception("insights_nightly_user_failed user=%s", user_id)
        return None


async def _run_global_expire(*, now: datetime) -> ExpireStats:
    async with AsyncSessionLocal() as db:
        stats = await expire_stale(db, now=now)
        await db.commit()
        return stats


async def _refresh_dashboard_views() -> bool:
    try:
        async with AsyncSessionLocal() as db:
            await refresh_dashboard_materialized_views(db)
            await db.commit()
        return True
    except Exception:
        log.exception("dashboard_materialized_refresh_failed")
        return False


async def run_nightly_for_all_users(
    *, now: datetime | None = None
) -> NightlyWorkerStats:
    effective_now = now or datetime.now(timezone.utc)
    summary = NightlyWorkerStats()

    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(User.id).where(User.status == "active")
        )
        user_ids = [row[0] for row in rows.fetchall()]
    summary.users_scanned = len(user_ids)
    log.info("insights_nightly_started users=%d", summary.users_scanned)

    for user_id in user_ids:
        per_user = await _run_one_user(user_id=user_id, now=effective_now)
        if per_user is None:
            summary.users_failed += 1
            summary.failed_user_ids.append(str(user_id))
            continue
        summary.users_succeeded += 1
        summary.persisted_created += per_user.persisted_created
        summary.persisted_updated += per_user.persisted_updated
        summary.persisted_reinforced += per_user.persisted_reinforced
        summary.persisted_skipped_locked += per_user.persisted_skipped_locked
        summary.persisted_skipped_user_override += (
            per_user.persisted_skipped_user_override
        )
        summary.gaps_created += per_user.gaps_created
        summary.gaps_updated += per_user.gaps_updated
        summary.gaps_reinforced += per_user.gaps_reinforced
        summary.gaps_expired_unsupported += per_user.gaps_expired_unsupported

    expire_stats = await _run_global_expire(now=effective_now)
    summary.expired_active = expire_stats.expired_active
    summary.raw_quotes_redacted = expire_stats.raw_quotes_redacted
    summary.hard_deleted = expire_stats.hard_deleted
    summary.dashboard_materialized_refreshed = await _refresh_dashboard_views()

    log.info(
        "insights_nightly_done users=%d ok=%d failed=%d "
        "created=%d updated=%d reinforced=%d gaps_created=%d "
        "expired_active=%d raw_quotes_redacted=%d hard_deleted=%d "
        "dashboard_mv_refreshed=%s",
        summary.users_scanned,
        summary.users_succeeded,
        summary.users_failed,
        summary.persisted_created,
        summary.persisted_updated,
        summary.persisted_reinforced,
        summary.gaps_created,
        summary.expired_active,
        summary.raw_quotes_redacted,
        summary.hard_deleted,
        summary.dashboard_materialized_refreshed,
    )
    return summary


async def main() -> None:
    setup_logging("INFO")
    await run_nightly_for_all_users()


if __name__ == "__main__":
    asyncio.run(main())
