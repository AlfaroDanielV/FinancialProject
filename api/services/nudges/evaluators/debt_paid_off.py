"""debt_paid_off evaluator (Phase 8 B5 — earned-celebration layer).

Fires when a debt is gone: its ``current_balance`` reached zero. Killing a debt
is one of the most motivating financial wins, and today it's a silent number
change — this turns it into an earned, voseo moment.

Two paths reach a zero balance:
  - ``record_payment`` brings the balance to 0 (the debt may stay active).
  - the user then deletes the debt (``archived=True``) — delete leaves the
    balance untouched, so a paid-then-deleted debt is still ``balance <= 0``.
There is deliberately NO ``archived`` filter, so both paths qualify; the dedup
key ``debt_paid:{debt_id}`` collapses the record_payment-to-0 signal and the
later delete into ONE celebration. An archived-but-UNPAID debt (balance > 0, a
write-off/abandonment) is not a win and is excluded by the balance condition.

Read-only — no writes, no LLM. The orchestrator inserts + dedups.

Priority: 'normal' (celebrations never bypass quiet hours / rate limit).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....models.debt import Debt
from .base import NudgeCandidate


class DebtPaidOffEvaluator:
    nudge_type = "debt_paid_off"

    async def evaluate(
        self,
        session: AsyncSession,
        now: datetime,
        *,
        user_id: Optional[uuid.UUID] = None,
    ) -> list[NudgeCandidate]:
        conditions = [Debt.current_balance <= 0]
        if user_id is not None:
            conditions.append(Debt.user_id == user_id)

        rows = (
            await session.execute(select(Debt).where(and_(*conditions)))
        ).scalars().all()

        candidates: list[NudgeCandidate] = []
        for debt in rows:
            payload: dict[str, Any] = {
                "debt_id": str(debt.id),
                "name": debt.name,
                "currency": debt.currency,
                "original_amount": f"{debt.original_amount:.2f}",
            }
            candidates.append(
                NudgeCandidate(
                    user_id=debt.user_id,
                    dedup_key=f"debt_paid:{debt.id}",
                    payload=payload,
                    priority="normal",
                )
            )
        return candidates
