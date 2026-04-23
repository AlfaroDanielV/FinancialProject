"""State transitions on user_nudges.

The REST router and the Telegram dispatcher (Bloque 9) both call these
helpers so the state machine lives in exactly one place.

Transitions:
    pending|sent → dismissed via mark_dismissed
    pending|sent → acted_on  via mark_acted_on

Auto-silence: every mark_dismissed checks whether this dismissal, combined
with prior dismissals within SILENCE_LOOKBACK_DAYS, crosses the
SILENCE_DISMISS_THRESHOLD. When it does AND no silence is currently active
for this (user, nudge_type), a silence row is inserted for
SILENCE_DURATION_DAYS.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user_nudge import UserNudge, UserNudgeSilence
from .policy import (
    REASON_AUTO_DISMISSED_2X,
    SILENCE_DISMISS_THRESHOLD,
    SILENCE_DURATION_DAYS,
    SILENCE_LOOKBACK_DAYS,
)


log = logging.getLogger("nudges.actions")


@dataclass
class DismissResult:
    nudge: UserNudge
    silence_created: bool


async def _load_nudge_for_user(
    session: AsyncSession, *, user_id: uuid.UUID, nudge_id: uuid.UUID
) -> UserNudge:
    result = await session.execute(
        select(UserNudge).where(
            and_(UserNudge.id == nudge_id, UserNudge.user_id == user_id)
        )
    )
    nudge = result.scalar_one_or_none()
    if nudge is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nudge no encontrado.",
        )
    return nudge


async def _has_active_silence(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    nudge_type: str,
    now: datetime,
) -> bool:
    stmt = (
        select(UserNudgeSilence.id)
        .where(
            UserNudgeSilence.user_id == user_id,
            UserNudgeSilence.nudge_type == nudge_type,
            UserNudgeSilence.silenced_until > now,
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def mark_dismissed(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    nudge_id: uuid.UUID,
    now: Optional[datetime] = None,
) -> DismissResult:
    """Flip the nudge to dismissed. Caller commits.

    Re-dismissing an already-dismissed nudge is a no-op and does NOT
    re-count toward the silence threshold — otherwise a user hitting the
    button twice would artificially trip silence. The count only looks at
    rows whose dismissed_at falls inside the lookback window.
    """
    effective_now = now or datetime.now(timezone.utc)

    nudge = await _load_nudge_for_user(
        session, user_id=user_id, nudge_id=nudge_id
    )

    if nudge.status != "dismissed":
        nudge.status = "dismissed"
        nudge.dismissed_at = effective_now
        await session.flush()

    # Count dismissals in lookback window (excluding pre-existing silenced
    # state from affecting whether *this* dismiss should trigger one).
    lookback_from = effective_now - timedelta(days=SILENCE_LOOKBACK_DAYS)
    count_stmt = (
        select(func.count())
        .select_from(UserNudge)
        .where(
            UserNudge.user_id == user_id,
            UserNudge.nudge_type == nudge.nudge_type,
            UserNudge.status == "dismissed",
            UserNudge.dismissed_at >= lookback_from,
        )
    )
    dismiss_count = (await session.execute(count_stmt)).scalar_one()

    silence_created = False
    if dismiss_count >= SILENCE_DISMISS_THRESHOLD:
        already_silenced = await _has_active_silence(
            session,
            user_id=user_id,
            nudge_type=nudge.nudge_type,
            now=effective_now,
        )
        if not already_silenced:
            silence = UserNudgeSilence(
                user_id=user_id,
                nudge_type=nudge.nudge_type,
                silenced_until=effective_now + timedelta(days=SILENCE_DURATION_DAYS),
                reason=REASON_AUTO_DISMISSED_2X,
            )
            session.add(silence)
            await session.flush()
            silence_created = True
            log.info(
                "auto-silence nudge_type=%s user=%s until=%s",
                nudge.nudge_type,
                user_id,
                silence.silenced_until,
            )

    return DismissResult(nudge=nudge, silence_created=silence_created)


async def mark_acted_on(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    nudge_id: uuid.UUID,
    now: Optional[datetime] = None,
) -> UserNudge:
    """Flip the nudge to acted_on. Caller commits. Idempotent."""
    effective_now = now or datetime.now(timezone.utc)
    nudge = await _load_nudge_for_user(
        session, user_id=user_id, nudge_id=nudge_id
    )
    if nudge.status != "acted_on":
        nudge.status = "acted_on"
        nudge.acted_on_at = effective_now
        await session.flush()
    return nudge
