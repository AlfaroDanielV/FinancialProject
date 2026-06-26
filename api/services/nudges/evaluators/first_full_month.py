"""first_full_month evaluator (Phase 8 B5 — earned-celebration layer).

Fires the first time the user has a CashflowSnapshot on file — i.e. they tracked
their finances long enough for the nightly job to freeze a monthly picture. It's
the "you built the habit" peak that the silent snapshot pipeline never surfaced.

Read-only — no writes, no LLM. It reads the FROZEN snapshot table (the same rows
the nightly worker captures), so it can never fabricate. The orchestrator inserts
+ dedups.

Dedup key: ``first_full_month:{user_id}``. Once ever — the key has no period, so
the milestone fires a single time across every event-trigger + nightly re-run.

Per the Phase 8 B5 spec the trigger is simply "the user's FIRST CashflowSnapshot
period exists"; the earliest period is carried in the payload for the copy.

Priority: 'normal' (celebrations never bypass quiet hours / rate limit).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....models.snapshot import CashflowSnapshot
from .base import NudgeCandidate


class FirstFullMonthEvaluator:
    nudge_type = "first_full_month"

    async def evaluate(
        self,
        session: AsyncSession,
        now: datetime,
        *,
        user_id: Optional[uuid.UUID] = None,
    ) -> list[NudgeCandidate]:
        # One row per user that has any cashflow snapshot, carrying the earliest
        # period. Grouping keeps the unscoped sweep correct; the scoped call adds
        # the user filter.
        stmt = select(
            CashflowSnapshot.user_id,
            func.min(CashflowSnapshot.period),
        ).group_by(CashflowSnapshot.user_id)
        if user_id is not None:
            stmt = stmt.where(CashflowSnapshot.user_id == user_id)

        rows = (await session.execute(stmt)).all()

        candidates: list[NudgeCandidate] = []
        for uid, first_period in rows:
            payload: dict[str, Any] = {"period": first_period}
            candidates.append(
                NudgeCandidate(
                    user_id=uid,
                    dedup_key=f"first_full_month:{uid}",
                    payload=payload,
                    priority="normal",
                )
            )
        return candidates
