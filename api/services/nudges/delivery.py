"""Nudge delivery worker.

Reads pending user_nudges, applies the four anti-saturation filters, asks
the LLM to phrase, sends via the Telegram channel, and marks the nudge
as sent.

Filter order (matches policy.py doc):
    1. Quiet hours  (rule 3)   → throttled_quiet_hours
    2. Silence      (rule 2)   → throttled_silenced
    3. Rate limit   (rule 1)   → throttled_rate_limit
    4. LLM + send              → sent | failed

Dedup (rule 4) already ran at evaluator/orchestrator time, so the delivery
worker never worries about duplicates — the rows in front of it are unique
by construction.

Rate limit scope: per-user, per-run. A normal-priority nudge is throttled
if (a) we already sent a prior normal-priority nudge in the LAST
RATE_LIMIT_WINDOW_HOURS, or (b) we already sent one in this same run.
High-priority nudges bypass both checks — and they don't count against
future rate limits either, so a burst of HIGH doesn't starve NORMAL.

On send failure: we log, count `failed`, and leave status='pending'. The
next run retries. No backoff / no retry budget in this phase — the spec
says "Si falla el envío, no reintentar en este phase; próximo run lo
intentará."
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user import User
from ...models.user_nudge import UserNudge, UserNudgeSilence
from ...schemas.nudges import NudgeDeliveryResult
from .phrasing import (
    SYSTEM_PROMPT,
    NudgePhrasingClient,
    PhrasingClientError,
    build_user_prompt,
)
from .policy import (
    RATE_LIMIT_WINDOW_HOURS,
    is_in_quiet_hours,
)


log = logging.getLogger("nudges.delivery")


# ── button catalog (deterministic per nudge_type) ────────────────────────────
# Three buttons max per WhatsApp's future portability constraint. Labels
# are voseo CR to match the rest of the bot. `verb` is the callback tag
# the inline-keyboard handler consumes.


@dataclass
class NudgeButton:
    label: str
    verb: str  # 'act' | 'dismiss' | 'later'


_BUTTONS: dict[str, list[NudgeButton]] = {
    "missing_income": [
        NudgeButton("Agregar ahora", "act"),
        NudgeButton("Más tarde", "later"),
        NudgeButton("No mostrar más", "dismiss"),
    ],
    "stale_pending_confirmation": [
        NudgeButton("Sí, agregar", "act"),
        NudgeButton("Descartar", "dismiss"),
        NudgeButton("Más tarde", "later"),
    ],
    "upcoming_bill": [
        NudgeButton("Ya pagué", "act"),
        NudgeButton("Recordame mañana", "later"),
        NudgeButton("Descartar", "dismiss"),
    ],
}


def buttons_for(nudge_type: str) -> list[NudgeButton]:
    return _BUTTONS.get(nudge_type, [])


# ── send abstraction ─────────────────────────────────────────────────────────
# The delivery worker calls `send_fn(NudgeMessage) -> bool`. In prod the
# implementation calls the aiogram bot; in tests a fake records the call.


@dataclass
class NudgeMessage:
    nudge_id: uuid.UUID
    chat_id: int
    text: str
    buttons: list[NudgeButton] = field(default_factory=list)


SendFn = Callable[[NudgeMessage], Awaitable[bool]]


# ── DB helpers ───────────────────────────────────────────────────────────────


async def _rate_limited_by_db(
    session: AsyncSession, *, user_id: uuid.UUID, now: datetime
) -> bool:
    """True if the user already received a normal-priority nudge within
    the RATE_LIMIT_WINDOW_HOURS window (from any prior run)."""
    window_start = now - timedelta(hours=RATE_LIMIT_WINDOW_HOURS)
    stmt = (
        select(UserNudge.id)
        .where(
            UserNudge.user_id == user_id,
            UserNudge.status == "sent",
            UserNudge.priority == "normal",
            UserNudge.sent_at >= window_start,
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def _active_silence(
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


async def _load_pending(
    session: AsyncSession, *, user_id: uuid.UUID
) -> list[UserNudge]:
    """Pending nudges for the user, HIGH first, then oldest first. The
    VARCHAR ordering on 'priority' happens to sort 'high' < 'normal'
    alphabetically, but we CASE it explicitly so the code reads clearly."""
    stmt = (
        select(UserNudge)
        .where(
            and_(
                UserNudge.user_id == user_id,
                UserNudge.status == "pending",
            )
        )
        .order_by(
            # 'high' → 0, 'normal' → 1
            (UserNudge.priority != "high"),
            UserNudge.created_at.asc(),
        )
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ── main entry ───────────────────────────────────────────────────────────────


async def deliver_all(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    phrasing_client: NudgePhrasingClient,
    send_fn: SendFn,
    model: str,
    now: Optional[datetime] = None,
) -> NudgeDeliveryResult:
    """Process this user's pending nudges. Caller commits."""
    effective_now = now or datetime.now(timezone.utc)

    user = await session.get(User, user_id)
    if user is None:
        return NudgeDeliveryResult()  # user vanished; nothing to do

    pending = await _load_pending(session, user_id=user_id)
    if not pending:
        return NudgeDeliveryResult()

    result = NudgeDeliveryResult(processed=len(pending))
    sent_normal_in_run = False

    # Quiet hours are user-wide; compute once.
    in_quiet = is_in_quiet_hours(effective_now, user.timezone)

    for nudge in pending:
        is_high = nudge.priority == "high"

        # 1. Quiet hours (HIGH bypasses? Spec says quiet hours applies to
        # all. We follow the spec — nothing is delivered 21:00-07:00.)
        if in_quiet:
            result.throttled_quiet_hours += 1
            continue

        # 2. Silence (live re-check — may have been inserted between
        # evaluator and delivery).
        if await _active_silence(
            session,
            user_id=user_id,
            nudge_type=nudge.nudge_type,
            now=effective_now,
        ):
            result.throttled_silenced += 1
            continue

        # 3. Rate limit (HIGH bypasses).
        if not is_high:
            if sent_normal_in_run:
                result.throttled_rate_limit += 1
                continue
            if await _rate_limited_by_db(
                session, user_id=user_id, now=effective_now
            ):
                result.throttled_rate_limit += 1
                continue

        # 4. Phrase + send.
        if user.telegram_user_id is None:
            log.warning(
                "deliver_all: user %s has no telegram_user_id; skipping nudge %s",
                user_id,
                nudge.id,
            )
            result.failed += 1
            continue

        try:
            text = await phrasing_client.phrase(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_user_prompt(nudge.nudge_type, nudge.payload),
                model=model,
            )
        except PhrasingClientError as e:
            log.warning(
                "deliver_all: phrasing failed nudge=%s err=%s", nudge.id, e
            )
            result.failed += 1
            continue

        message = NudgeMessage(
            nudge_id=nudge.id,
            chat_id=user.telegram_user_id,
            text=text,
            buttons=buttons_for(nudge.nudge_type),
        )
        try:
            ok = await send_fn(message)
        except Exception as e:  # noqa: BLE001 — any channel error → failed
            log.warning(
                "deliver_all: send raised nudge=%s err=%s", nudge.id, e
            )
            ok = False

        if not ok:
            result.failed += 1
            continue

        # Mark sent. No commit here; the caller commits once at the end
        # (Phase 4 /jobs/* pattern).
        nudge.status = "sent"
        nudge.sent_at = effective_now
        nudge.delivery_channel = "telegram"
        await session.flush()

        result.sent += 1
        if not is_high:
            sent_normal_in_run = True

    log.info(
        "deliver_all user=%s processed=%d sent=%d quiet=%d silenced=%d rate=%d failed=%d",
        user_id,
        result.processed,
        result.sent,
        result.throttled_quiet_hours,
        result.throttled_silenced,
        result.throttled_rate_limit,
        result.failed,
    )
    return result
