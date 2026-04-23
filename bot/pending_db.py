"""Durable persistence for staged proposals (pending_confirmations).

Redis (bot/pending.py) is still the source of truth for the live session:
short TTL, fast lookups, driven by the immediate confirm/cancel flow.
Postgres is the audit + nudge surface: Phase 5d's stale_pending evaluator
reads this table for proposals that decayed past 48h without resolution.

One row per ProposeAction. Resolved on confirm / reject / edit / cancel
/ superseded (new proposal while one is still open). The evaluator
ignores resolved rows; the UNIQUE (user_id, dedup_key) on user_nudges
guarantees a single stale_pending nudge per unresolved row.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.pending_confirmation import PendingConfirmation

from .pending import PendingAction


async def persist_pending_confirmation(
    *,
    session: AsyncSession,
    user_id: uuid.UUID,
    pending: PendingAction,
    channel: str = "telegram",
    channel_message_id: Optional[str] = None,
) -> uuid.UUID:
    """INSERT a new row, return its id. Caller commits."""
    row = PendingConfirmation(
        user_id=user_id,
        short_id=pending.short_id,
        channel=channel,
        channel_message_id=channel_message_id,
        action_type=pending.action_type,
        proposed_action={
            "action_type": pending.action_type,
            "summary_es": pending.summary_es,
            "payload": pending.payload,
        },
    )
    session.add(row)
    await session.flush()
    return row.id


async def mark_previous_superseded(
    *, session: AsyncSession, user_id: uuid.UUID, now: Optional[datetime] = None
) -> int:
    """Mark every unresolved proposal for this user as 'superseded'.
    Called before a fresh ProposeAction is persisted so at most one row
    is ever in the 'waiting for user' state per user.

    Returns the number of rows flipped — useful for logging only."""
    ts = now or datetime.now(timezone.utc)
    stmt = (
        update(PendingConfirmation)
        .where(
            PendingConfirmation.user_id == user_id,
            PendingConfirmation.resolved_at.is_(None),
        )
        .values(resolved_at=ts, resolution="superseded")
    )
    result = await session.execute(stmt)
    return result.rowcount or 0


async def mark_confirmation_resolved(
    *,
    session: AsyncSession,
    confirmation_id: uuid.UUID,
    resolution: str,
    now: Optional[datetime] = None,
) -> bool:
    """Stamp resolved_at + resolution on a single row. No-op if already
    resolved (so stale button presses don't flip an earlier outcome).
    Returns True when a row was actually updated."""
    ts = now or datetime.now(timezone.utc)
    stmt = (
        update(PendingConfirmation)
        .where(
            PendingConfirmation.id == confirmation_id,
            PendingConfirmation.resolved_at.is_(None),
        )
        .values(resolved_at=ts, resolution=resolution)
    )
    result = await session.execute(stmt)
    return (result.rowcount or 0) > 0


async def resolve_from_pending(
    *,
    session: AsyncSession,
    pending: PendingAction,
    resolution: str,
    now: Optional[datetime] = None,
) -> bool:
    """Resolve the DB row attached to this Redis pending. Safe when
    confirmation_id is None (pre-5d row in Redis) — returns False."""
    if not pending.confirmation_id:
        return False
    try:
        cid = uuid.UUID(pending.confirmation_id)
    except (TypeError, ValueError):
        return False
    return await mark_confirmation_resolved(
        session=session, confirmation_id=cid, resolution=resolution, now=now
    )
