"""under_budget_month evaluator (Phase 8 B5 — earned-celebration layer).

Fires when the user closed a month INSIDE a sobre's limit: an EnvelopeSnapshot
row for a CLOSED month whose frozen ``over_limit`` is False (and that actually
saw spend). Staying under budget is a real, earned win the app never marked.

CRITICAL — reads the FROZEN snapshot, NEVER live spend. The live envelope
summary recomputes from current limits/transactions, so reading it would let a
later limit edit (or new spend in a new month) silently re-fire — or un-fire — a
past month's celebration. The snapshot is the month's permanent record.

A "closed month" = period strictly before the current month (``now``). The
nightly job recaptures the just-closed period during the first days of a month,
so the frozen row is complete by the time this looks at it.

``spent > 0`` guard: an untouched envelope (limit set, nothing spent) is
``over_limit=False`` but staying under a budget you never used isn't earned.

Read-only — no writes, no LLM. The orchestrator inserts + dedups.

Dedup key: ``under_budget:{envelope_id|snapshot_id}:{period}``. One celebration
per envelope per closed month; the envelope_id can be NULL (FK SET NULL after a
hard delete) so the snapshot id backstops uniqueness.

Priority: 'normal' (celebrations never bypass quiet hours / rate limit).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....models.snapshot import EnvelopeSnapshot
from .base import NudgeCandidate


class UnderBudgetMonthEvaluator:
    nudge_type = "under_budget_month"

    async def evaluate(
        self,
        session: AsyncSession,
        now: datetime,
        *,
        user_id: Optional[uuid.UUID] = None,
    ) -> list[NudgeCandidate]:
        current_period = f"{now.year:04d}-{now.month:02d}"

        conditions = [
            EnvelopeSnapshot.over_limit.is_(False),
            EnvelopeSnapshot.period < current_period,  # closed month only
            EnvelopeSnapshot.spent > 0,
        ]
        if user_id is not None:
            conditions.append(EnvelopeSnapshot.user_id == user_id)

        rows = (
            await session.execute(
                select(EnvelopeSnapshot).where(and_(*conditions))
            )
        ).scalars().all()

        candidates: list[NudgeCandidate] = []
        for snap in rows:
            key_id = snap.envelope_id or snap.id
            payload: dict[str, Any] = {
                "envelope_id": str(snap.envelope_id) if snap.envelope_id else None,
                "name": snap.name,
                "envelope_class": snap.envelope_class,
                "currency": snap.currency,
                "period": snap.period,
                "limit_amount": f"{snap.limit_amount:.2f}",
                "spent": f"{snap.spent:.2f}",
            }
            candidates.append(
                NudgeCandidate(
                    user_id=snap.user_id,
                    dedup_key=f"under_budget:{key_id}:{snap.period}",
                    payload=payload,
                    priority="normal",
                )
            )
        return candidates
