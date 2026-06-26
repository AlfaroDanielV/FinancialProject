"""goal_achieved evaluator (Phase 8 B5 — earned-celebration layer).

Fires when a savings goal reaches its finish line: the user explicitly marked it
``status='achieved'`` OR its ``current_amount`` reached/passed ``target_amount``.
Abandoned goals never celebrate (an over-target abandoned goal isn't a win).

This is the positive twin of the problem-focused evaluators: the *decision* to
celebrate is deterministic (a goal at/over target is an objective milestone —
mirrors "the LLM never decides whether/when to nudge"); the LLM only PHRASES.

Read-only — no writes, no LLM, no fabricated numbers. The orchestrator inserts +
dedups; the payload carries the real goal name + amount.

Dedup key: ``goal_achieved:{goal_id}``. One celebration per goal, ever — the key
has no period, so reaching the target is celebrated once and never re-fires (even
across event-trigger + nightly re-runs).

Priority: 'normal'. A celebration must NOT bypass quiet hours / rate limit — a
3am "you hit your goal!" would be worse than waiting for morning.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....models.goal import Goal
from .base import NudgeCandidate


class GoalAchievedEvaluator:
    nudge_type = "goal_achieved"

    async def evaluate(
        self,
        session: AsyncSession,
        now: datetime,
        *,
        user_id: Optional[uuid.UUID] = None,
    ) -> list[NudgeCandidate]:
        conditions = [
            Goal.status != "abandoned",
            or_(
                Goal.status == "achieved",
                and_(
                    Goal.target_amount > 0,
                    Goal.current_amount >= Goal.target_amount,
                ),
            ),
        ]
        if user_id is not None:
            conditions.append(Goal.user_id == user_id)

        rows = (
            await session.execute(select(Goal).where(and_(*conditions)))
        ).scalars().all()

        candidates: list[NudgeCandidate] = []
        for goal in rows:
            payload: dict[str, Any] = {
                "goal_id": str(goal.id),
                "name": goal.name,
                "currency": goal.target_currency,
                "target_amount": f"{goal.target_amount:.2f}",
            }
            candidates.append(
                NudgeCandidate(
                    user_id=goal.user_id,
                    dedup_key=f"goal_achieved:{goal.id}",
                    payload=payload,
                    priority="normal",
                )
            )
        return candidates
