"""shadow_review_pending evaluator.

Reminds a user about Gmail-parsed transactions that landed in shadow status
(status='shadow', source='gmail') and have sat UNREVIEWED long enough that the
at-scan ping is no longer fresh in mind.

The at-scan Gmail notifier (api/services/gmail/notifier.py) already pings ONCE
the day new shadow rows arrive; this is the periodic *follow-up* for rows the
user ignored. It re-reminds every SHADOW_REVIEW_PENDING_REREMIND_INTERVAL_DAYS,
capped downstream by the four anti-saturation rules. Once every shadow row is
reviewed/confirmed there is no candidate, so the reminders stop on their own.

Population: ALL pending shadow rows for the user (status='shadow',
    source='gmail') — mirrors gmail/shadow_review.py::list_shadow exactly so the
    nudge's count matches what the review screen shows.

Gate (per user): fire only once the OLDEST pending shadow row has aged past
    SHADOW_REVIEW_PENDING_MIN_AGE_DAYS — fresh same-day arrivals are the
    at-scan ping's job, not this reminder's.

Dedup key: shadow_review_pending:{user_id}:{age_bucket}, where age_bucket =
    oldest_age_days // SHADOW_REVIEW_PENDING_REREMIND_INTERVAL_DAYS. Bucketing
    on the OLDEST row's age (not the calendar) means consecutive reminders are
    spaced exactly one interval apart and the cadence is timezone-independent —
    a calendar-aligned bucket would flip at a fixed grid boundary and could
    re-fire a day early. A 6h scheduler pass inside the same age window is a
    no-op (ON CONFLICT DO NOTHING).

Priority: normal (a standing chore, not a timed emergency).

Deterministic — no LLM. The LLM only PHRASES the resulting nudge.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....models.transaction import Transaction
from ..policy import (
    SHADOW_REVIEW_PENDING_MIN_AGE_DAYS,
    SHADOW_REVIEW_PENDING_REREMIND_INTERVAL_DAYS,
)
from .base import NudgeCandidate


def _aware(dt: datetime) -> datetime:
    """Coerce a naive datetime to aware UTC. The worker passes an aware `now`
    and timestamptz columns read back aware, but the model's default is a naive
    utcnow(), so a same-session-cached row can surface naive."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class ShadowReviewPendingEvaluator:
    nudge_type = "shadow_review_pending"

    async def evaluate(
        self,
        session: AsyncSession,
        now: datetime,
        *,
        user_id: Optional[uuid.UUID] = None,
    ) -> list[NudgeCandidate]:
        now = _aware(now)
        cutoff = now - timedelta(days=SHADOW_REVIEW_PENDING_MIN_AGE_DAYS)
        interval = max(1, SHADOW_REVIEW_PENDING_REREMIND_INTERVAL_DAYS)

        # Mirror gmail/shadow_review.py::_shadow_where (the canonical "pending
        # shadow" set the review screen shows) — NO age/archived filter here, so
        # our count matches what the user sees there. The age threshold is only
        # the gate below, applied to the oldest row.
        conditions = [
            Transaction.status == "shadow",
            Transaction.source == "gmail",
        ]
        if user_id is not None:
            conditions.append(Transaction.user_id == user_id)

        rows = (
            await session.execute(
                select(Transaction)
                .where(and_(*conditions))
                .order_by(Transaction.created_at.asc())
            )
        ).scalars().all()

        # Aggregate per user — one reminder names the whole pending batch.
        by_user: dict[uuid.UUID, list[Transaction]] = {}
        for txn in rows:
            by_user.setdefault(txn.user_id, []).append(txn)

        candidates: list[NudgeCandidate] = []
        for uid, txns in by_user.items():
            oldest = _aware(txns[0].created_at)  # asc order → first is oldest
            # Gate: don't nag about emails that only just arrived; the at-scan
            # ping already covered those.
            if oldest >= cutoff:
                continue

            oldest_age_days = max(0, (now - oldest).days)
            # Re-remind bucket keyed on the oldest row's age, so reminders are
            # spaced exactly `interval` days apart regardless of calendar/tz.
            age_bucket = oldest_age_days // interval
            merchants = list(
                dict.fromkeys(t.merchant for t in txns if t.merchant)
            )[:3]
            payload: dict[str, Any] = {
                "count": len(txns),
                "oldest_age_days": oldest_age_days,
                "sample_merchants": merchants,
            }
            candidates.append(
                NudgeCandidate(
                    user_id=uid,
                    dedup_key=f"shadow_review_pending:{uid}:{age_bucket}",
                    payload=payload,
                    priority="normal",
                )
            )
        return candidates
