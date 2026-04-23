"""upcoming_bill evaluator.

Wraps (does not replace) Phase 4's notification_events. For every pending
notification_event whose underlying due_date / event_date falls in the
next UPCOMING_BILL_WINDOW_HOURS, emit one nudge carrying the payload
snapshot the LLM needs for phrasing.

notification_events stays the canonical "this reminder exists" record;
user_nudges is the delivery + engagement layer on top. The evaluator
reads — it does NOT mutate notification_events. Acknowledgement of the
underlying event happens elsewhere (act / dismiss endpoints), so a single
underlying event can be referenced by at most one nudge at a time
(enforced by the dedup_key).

Condition:
    notification_events.status = 'pending'
    AND the snapshot's due_date / event_date is within
        [today, today + UPCOMING_BILL_WINDOW_HOURS/24 days].

Priority: 'high' when due_date is within UPCOMING_BILL_HIGH_PRIORITY_HOURS
(evaluated as today..today+1 on the calendar-date model — the Phase 4
snapshot doesn't carry hour-of-day). 'normal' otherwise.

Dedup key: upcoming_bill:{notification_event_id}. One nudge per
notification_event, stable across re-runs.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....models.notification_event import NotificationEvent
from ..policy import (
    UPCOMING_BILL_HIGH_PRIORITY_HOURS,
    UPCOMING_BILL_WINDOW_HOURS,
)
from .base import NudgeCandidate


def _due_date_from_snapshot(snapshot: dict[str, Any]) -> Optional[date]:
    raw = snapshot.get("due_date") or snapshot.get("event_date")
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


class UpcomingBillEvaluator:
    nudge_type = "upcoming_bill"

    async def evaluate(
        self,
        session: AsyncSession,
        now: datetime,
        *,
        user_id: Optional[uuid.UUID] = None,
    ) -> list[NudgeCandidate]:
        today = now.date()
        window_days = max(1, UPCOMING_BILL_WINDOW_HOURS // 24)
        high_prio_days = max(1, UPCOMING_BILL_HIGH_PRIORITY_HOURS // 24)
        window_end = today + timedelta(days=window_days)
        high_prio_cutoff = today + timedelta(days=high_prio_days)

        stmt = select(NotificationEvent).where(
            NotificationEvent.status == "pending"
        )
        if user_id is not None:
            stmt = stmt.where(NotificationEvent.user_id == user_id)
        result = await session.execute(stmt)
        rows = result.scalars().all()

        candidates: list[NudgeCandidate] = []
        for ev in rows:
            due = _due_date_from_snapshot(ev.payload_snapshot)
            if due is None:
                continue
            if not (today <= due <= window_end):
                continue
            priority = "high" if due <= high_prio_cutoff else "normal"
            payload: dict[str, Any] = {
                "notification_event_id": str(ev.id),
                "trigger_date": ev.trigger_date.isoformat(),
                "advance_days": ev.advance_days,
                "snapshot": ev.payload_snapshot,
                "due_date": due.isoformat(),
            }
            candidates.append(
                NudgeCandidate(
                    user_id=ev.user_id,
                    dedup_key=f"upcoming_bill:{ev.id}",
                    payload=payload,
                    priority=priority,
                    source_notification_event_id=ev.id,
                )
            )
        return candidates
