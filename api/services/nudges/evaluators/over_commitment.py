"""over_commitment evaluator (Phase 7, surface 3 — proactive pushback).

Fires when a user's recurring obligations already swallow most of their
income, BEFORE they propose a new goal or purchase. Where the chat tool and
the goal-creation gate react to a question the user asked, this is the
proactive surface: the agent raises the flag on its own.

Condition (deterministic, reuses the affordability engine — NOT re-derived):
    inputs = gather_affordability_inputs(db, user)   # income, fixed, debt
    committed = fixed_bills + debt_minimums
    fire when monthly_income is known and > 0
         and committed / monthly_income >= OVER_COMMITMENT_RATIO

Reusing ``api/services/finance/affordability.py`` keeps this surface's notion
of "income / fixed / commitments" byte-identical to the chat tool, the goal
gate, and the envelope income line — there is exactly one place that decides
what those numbers are.

No income on file → no candidate (honest: we don't fabricate a disposable,
the same rule the engine and the missing_income evaluator already follow).

Dedup key: over_commitment:{user_id}:{YYYY-MM}. One nudge per user per
calendar month; the silence machinery handles a user who keeps dismissing.

Priority: always 'normal'. Over-commitment is a chronic state, not a timed
emergency — the monthly dedup paces it; it does not jump the rate limit.

The evaluator NEVER calculates feasibility copy: it ships the real numbers in
the payload and the phrasing LLM words them. "Rules decide; LLM explains."
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....models.user import User
from ...finance.affordability import gather_affordability_inputs
from ..policy import OVER_COMMITMENT_RATIO
from .base import NudgeCandidate


_RATIO = Decimal(str(OVER_COMMITMENT_RATIO))
_ZERO = Decimal("0")


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


class OverCommitmentEvaluator:
    nudge_type = "over_commitment"

    async def _users(
        self, session: AsyncSession, user_id: Optional[uuid.UUID]
    ) -> list[User]:
        """Active users in scope. Scoped to one when the caller passes a
        user_id (every caller does today); the unscoped sweep is reserved
        for a future cron and stays a simple active-user select."""
        if user_id is not None:
            user = await session.get(User, user_id)
            if user is None or user.status != "active":
                return []
            return [user]
        result = await session.execute(
            select(User).where(User.status == "active")
        )
        return list(result.scalars().all())

    async def evaluate(
        self,
        session: AsyncSession,
        now: datetime,
        *,
        user_id: Optional[uuid.UUID] = None,
    ) -> list[NudgeCandidate]:
        month_tag = f"{now.year:04d}-{now.month:02d}"
        candidates: list[NudgeCandidate] = []

        for user in await self._users(session, user_id):
            inputs = await gather_affordability_inputs(session, user=user)

            income = inputs.monthly_income
            # Honesty: no income on file (or non-positive) → no verdict.
            if income is None or income <= _ZERO:
                continue

            committed = inputs.monthly_fixed_expenses + inputs.monthly_debt_payments
            if committed / income < _RATIO:
                continue

            disposable = income - committed
            ratio_pct = int((committed / income * Decimal("100")).to_integral_value())
            payload: dict[str, Any] = {
                "currency": inputs.currency,
                "monthly_income": _money(income),
                "monthly_fixed_expenses": _money(inputs.monthly_fixed_expenses),
                "monthly_debt_payments": _money(inputs.monthly_debt_payments),
                "monthly_disposable": _money(disposable),
                "committed_ratio_pct": ratio_pct,
                "threshold_pct": int(_RATIO * 100),
                "excluded_variable_bills": inputs.excluded_variable_bills,
                "month_tag": month_tag,
            }
            candidates.append(
                NudgeCandidate(
                    user_id=user.id,
                    dedup_key=f"over_commitment:{user.id}:{month_tag}",
                    payload=payload,
                    priority="normal",
                )
            )
        return candidates
