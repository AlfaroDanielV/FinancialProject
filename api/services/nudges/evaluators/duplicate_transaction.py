"""duplicate_transaction evaluator (safety net).

Likely-duplicate expenses are normally flagged + nudged inline at capture by
`api/services/dedup/duplicate_detector.flag_and_notify`. This evaluator is the
batch safety net: it turns any `is_duplicate=True` row WITHOUT a nudge into one
(e.g. if the inline best-effort notify was rolled back, or a row was flagged by
a non-chat path). The orchestrator's ON CONFLICT (user_id, dedup_key) makes a
re-run idempotent against the inline-created nudge.

Deterministic — no LLM. The detector already decided what's a duplicate; this
only re-materializes the matched-row context for the payload.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....models.transaction import Transaction
from ...dedup.duplicate_detector import (
    _build_payload,
    find_likely_duplicate,
)
from .base import NudgeCandidate


class DuplicateTransactionEvaluator:
    nudge_type = "duplicate_transaction"

    async def evaluate(
        self,
        session: AsyncSession,
        now: datetime,
        *,
        user_id: Optional[uuid.UUID] = None,
    ) -> list[NudgeCandidate]:
        conditions = [
            Transaction.is_duplicate.is_(True),
            Transaction.status == "confirmed",
            Transaction.archived.is_(False),
            Transaction.amount < 0,
            Transaction.transfer_id.is_(None),
            Transaction.goal_id.is_(None),
        ]
        if user_id is not None:
            conditions.append(Transaction.user_id == user_id)

        rows = (
            await session.execute(select(Transaction).where(and_(*conditions)))
        ).scalars().all()

        candidates: list[NudgeCandidate] = []
        for txn in rows:
            matched = await find_likely_duplicate(
                session, user_id=txn.user_id, txn=txn
            )
            candidates.append(
                NudgeCandidate(
                    user_id=txn.user_id,
                    dedup_key=f"duplicate:{txn.id}",
                    payload=_build_payload(txn, matched),
                    priority="normal",
                )
            )
        return candidates
