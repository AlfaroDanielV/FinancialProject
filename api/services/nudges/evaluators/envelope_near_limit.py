"""envelope_near_limit evaluator (proactive spending-cap alert).

Fires when one of the user's spending-cap envelopes ("sobres") is nearly spent,
and again if it goes over its limit. Two stages per envelope per month:
    pct >= ENVELOPE_NEAR_LIMIT_RATIO and not over → stage "near"  (heads-up)
    over_limit (pct >= 1.0)                        → stage "over"  (you passed it)

Condition (deterministic, reuses the ONE engine):
    summary = compute_envelope_summary(session, user=user)
    for each envelope item -> read its rolled-up pct / over_limit.

Reusing ``api/services/envelopes.py::compute_envelope_summary`` is the whole
point: the alert reads the exact figure the on-screen bar shows, so the message
can never drift from what the user sees. (Same "reuse the engine" discipline as
the over_commitment evaluator reusing the affordability engine.)

Shared envelopes the user only *joined* (``is_shared``) are skipped — they are
display-only items belonging to another owner; only the owner is nudged.

Dedup key: envelope_near_limit:{envelope_id}:{YYYY-MM}:{stage}. The "near" alert
fires once; if the envelope later crosses its limit the "over" alert fires once
more — both reset with the monthly window. Priority 'normal' (a budget cap is a
heads-up, not a timed emergency; the monthly dedup + the 48h rate limit pace it).

The evaluator NEVER writes the user-facing text: it ships the real numbers in
the payload and the phrasing LLM (push) / the feed renderer (pull) word them.
"Rules decide; LLM explains."
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....models.user import User
from ...envelopes import compute_envelope_summary
from ..policy import ENVELOPE_NEAR_LIMIT_RATIO
from .base import NudgeCandidate


def _money(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "0.00"


class EnvelopeNearLimitEvaluator:
    nudge_type = "envelope_near_limit"

    async def _users(
        self, session: AsyncSession, user_id: Optional[uuid.UUID]
    ) -> list[User]:
        """Active users in scope. Scoped to one when the caller passes a
        user_id (every caller does today); the unscoped sweep is reserved for a
        future cron and stays a simple active-user select."""
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
            summary = await compute_envelope_summary(session, user=user)

            for env in summary.envelopes:
                # Display-only shared envelopes (joined, not owned) are never the
                # caller's to be alerted about.
                if getattr(env, "is_shared", False):
                    continue

                if env.over_limit:
                    stage = "over"
                elif env.pct >= ENVELOPE_NEAR_LIMIT_RATIO:
                    stage = "near"
                else:
                    continue

                payload: dict[str, Any] = {
                    "envelope_id": str(env.id),
                    "name": env.name,
                    "envelope_class": env.envelope_class,
                    "currency": env.currency,
                    "limit_amount": _money(env.limit_amount),
                    "spent": _money(env.spent),
                    "available": _money(env.available),
                    "pct": int(round(env.pct * 100)),
                    "stage": stage,
                    "month_tag": month_tag,
                }
                candidates.append(
                    NudgeCandidate(
                        user_id=user.id,
                        dedup_key=(
                            f"envelope_near_limit:{env.id}:{month_tag}:{stage}"
                        ),
                        payload=payload,
                        priority="normal",
                    )
                )
        return candidates
