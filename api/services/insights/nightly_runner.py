"""Phase 6c B10 — per-user nightly recompute.

Single source of truth shared by:
  - workers/insights_nightly.py (the ACA Job)
  - api/routers/admin_insights.py (manual admin trigger)
  - bot /recalcular_memoria command

Per-user contract: compute_all → persist (source='computed') →
detect_stated_observed_gaps. Expiry is a global sweep run once per
worker invocation, not per user — see workers/insights_nightly.py.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from api.services.insights.computed import compute_all
from api.services.insights.lifecycle import (
    GapDetectionStats,
    detect_stated_observed_gaps,
)
from api.services.insights.persister import PersistStats, persist_insights


log = logging.getLogger("api.services.insights.nightly_runner")


@dataclass
class NightlyRunStats:
    user_id: uuid.UUID
    insights_proposed: int = 0
    persisted_created: int = 0
    persisted_updated: int = 0
    persisted_reinforced: int = 0
    persisted_skipped_locked: int = 0
    persisted_skipped_user_override: int = 0
    gaps_emitted: int = 0
    gaps_created: int = 0
    gaps_updated: int = 0
    gaps_reinforced: int = 0
    gaps_expired_unsupported: int = 0


async def run_nightly_for_user(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    now: datetime | None = None,
) -> NightlyRunStats:
    """Recompute computed insights for one user, then refresh derived gaps.

    Caller owns the transaction. The function flushes through persister
    + detector but does NOT commit — workers commit per-user, the admin
    endpoint commits before returning.
    """
    effective_now = now or datetime.now(timezone.utc)

    proposed = await compute_all(session, user_id, as_of=effective_now.date())
    persist_stats: PersistStats = await persist_insights(
        session,
        user_id=user_id,
        insights=proposed,
        source="computed",
        now=effective_now,
    )
    gap_stats: GapDetectionStats = await detect_stated_observed_gaps(
        session,
        user_id=user_id,
        now=effective_now,
    )

    return NightlyRunStats(
        user_id=user_id,
        insights_proposed=len(proposed),
        persisted_created=persist_stats.created,
        persisted_updated=persist_stats.updated,
        persisted_reinforced=persist_stats.reinforced,
        persisted_skipped_locked=persist_stats.skipped_locked,
        persisted_skipped_user_override=persist_stats.skipped_user_override,
        gaps_emitted=gap_stats.emitted,
        gaps_created=gap_stats.created,
        gaps_updated=gap_stats.updated,
        gaps_reinforced=gap_stats.reinforced,
        gaps_expired_unsupported=gap_stats.expired_unsupported,
    )
