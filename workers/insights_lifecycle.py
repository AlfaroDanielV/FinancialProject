"""Standalone Phase 6c lifecycle worker.

This is the B5 maintenance job. The full computed nightly worker lands
in B10; for now this process handles lifecycle-only maintenance:
derived stated/observed gaps, raw quote redaction, and expired-row purge.

Run manually:

    uv run python -m workers.insights_lifecycle
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from api.database import AsyncSessionLocal
from api.logging_config import setup_logging
from api.models.user import User
from api.services.insights.lifecycle import (
    ExpireStats,
    GapDetectionStats,
    detect_stated_observed_gaps,
    expire_stale,
)


log = logging.getLogger("workers.insights_lifecycle")


@dataclass
class LifecycleWorkerStats:
    users_scanned: int = 0
    gaps_emitted: int = 0
    gaps_created: int = 0
    gaps_updated: int = 0
    gaps_reinforced: int = 0
    gaps_expired_unsupported: int = 0
    expired_active: int = 0
    raw_quotes_redacted: int = 0
    hard_deleted: int = 0


async def run_lifecycle_once(*, now: datetime | None = None) -> LifecycleWorkerStats:
    effective_now = now or datetime.now(timezone.utc)
    stats = LifecycleWorkerStats()

    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(User.id).where(User.status == "active")
        )
        user_ids = [row[0] for row in rows.fetchall()]
        stats.users_scanned = len(user_ids)

        for user_id in user_ids:
            gap_stats: GapDetectionStats = await detect_stated_observed_gaps(
                db,
                user_id=user_id,
                now=effective_now,
            )
            stats.gaps_emitted += gap_stats.emitted
            stats.gaps_created += gap_stats.created
            stats.gaps_updated += gap_stats.updated
            stats.gaps_reinforced += gap_stats.reinforced
            stats.gaps_expired_unsupported += gap_stats.expired_unsupported

        expire_stats: ExpireStats = await expire_stale(db, now=effective_now)
        stats.expired_active = expire_stats.expired_active
        stats.raw_quotes_redacted = expire_stats.raw_quotes_redacted
        stats.hard_deleted = expire_stats.hard_deleted
        await db.commit()

    return stats


async def main() -> None:
    setup_logging("INFO")
    stats = await run_lifecycle_once()
    log.info(
        "insights_lifecycle_done users=%d gaps_emitted=%d created=%d "
        "updated=%d reinforced=%d expired_unsupported=%d expired_active=%d "
        "raw_quotes_redacted=%d hard_deleted=%d",
        stats.users_scanned,
        stats.gaps_emitted,
        stats.gaps_created,
        stats.gaps_updated,
        stats.gaps_reinforced,
        stats.gaps_expired_unsupported,
        stats.expired_active,
        stats.raw_quotes_redacted,
        stats.hard_deleted,
    )


if __name__ == "__main__":
    asyncio.run(main())
