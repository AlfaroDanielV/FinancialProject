"""stale_pending_confirmation evaluator.

Fires when the bot proposed an action (expense/income to log) and the
user never confirmed or rejected it within STALE_PENDING_THRESHOLD_HOURS.

Why a dedicated Postgres table and not Redis:
    Pending proposals in Phase 5b live in Redis with a 5-minute TTL — far
    shorter than the 48h this rule requires. pending_confirmations
    (migration 0008) is the durable mirror: the dispatcher writes it at
    propose-time, marks it resolved at confirm/reject/edit/cancel time,
    and this evaluator reads the unresolved tail.

Condition: pending_confirmations row with
    resolved_at IS NULL AND created_at < now - STALE_PENDING_THRESHOLD_HOURS.

Dedup key: stale_pending:{pending_confirmation_id}. One nudge per proposal.

Priority: normal.

Payload: the snapshot needed for the LLM to phrase the nudge — the
proposed_action dict plus the created_at so the LLM can say "hace dos días".
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....models.pending_confirmation import PendingConfirmation
from ..policy import STALE_PENDING_THRESHOLD_HOURS
from .base import NudgeCandidate


class StalePendingEvaluator:
    nudge_type = "stale_pending_confirmation"

    async def evaluate(
        self,
        session: AsyncSession,
        now: datetime,
        *,
        user_id: Optional[uuid.UUID] = None,
    ) -> list[NudgeCandidate]:
        threshold = now - timedelta(hours=STALE_PENDING_THRESHOLD_HOURS)
        conditions = [
            PendingConfirmation.resolved_at.is_(None),
            PendingConfirmation.created_at < threshold,
        ]
        if user_id is not None:
            conditions.append(PendingConfirmation.user_id == user_id)
        stmt = select(PendingConfirmation).where(and_(*conditions))
        result = await session.execute(stmt)
        rows = result.scalars().all()

        candidates: list[NudgeCandidate] = []
        for row in rows:
            payload: dict[str, Any] = {
                "pending_confirmation_id": str(row.id),
                "short_id": row.short_id,
                "action_type": row.action_type,
                "channel": row.channel,
                "created_at": row.created_at.isoformat(),
                "proposed_action": row.proposed_action,
            }
            candidates.append(
                NudgeCandidate(
                    user_id=row.user_id,
                    dedup_key=f"stale_pending:{row.id}",
                    payload=payload,
                    priority="normal",
                )
            )
        return candidates
