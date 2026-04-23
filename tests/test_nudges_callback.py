"""Tests for the Telegram callback handler introduced in Phase 5d.

Covers `handle_nudge_callback`:
    - act → status=acted_on, reply is type-specific
    - dismiss → status=dismissed, silence_created flag drives the reply
    - dismiss on stale_pending_confirmation also closes the linked
      pending_confirmations row as 'rejected'
    - later → status=dismissed, neutral reply
    - unknown verbs / malformed callback_data / wrong-user → NUDGE_EXPIRED

We drive the pipeline directly with a fake User — the aiogram layer is a
thin adapter and has its own simulator path in the handler tests that
ship with 5b.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from api.models.pending_confirmation import PendingConfirmation
from api.models.user import User
from api.models.user_nudge import UserNudge, UserNudgeSilence
from bot import messages_es
from bot.pipeline import handle_nudge_callback


@dataclass
class _FakeRedis:
    """handle_nudge_callback doesn't touch Redis today, but the signature
    takes it for symmetry with handle_pending_callback. Just a placeholder."""


async def _make_nudge(
    session,
    user_id: uuid.UUID,
    *,
    nudge_type: str = "missing_income",
    payload: dict | None = None,
) -> uuid.UUID:
    nid = uuid.uuid4()
    session.add(
        UserNudge(
            id=nid,
            user_id=user_id,
            nudge_type=nudge_type,
            priority="normal",
            dedup_key=f"{nudge_type}:{uuid.uuid4()}",
            payload=payload or {},
            status="pending",
        )
    )
    await session.commit()
    return nid


async def _get_user(session, user_id: uuid.UUID) -> User:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one()


# ─────────────────────────────────────────────────────────────────────────────
# act
# ─────────────────────────────────────────────────────────────────────────────


async def test_callback_act_missing_income(db_with_user):
    session, user_id = db_with_user
    nid = await _make_nudge(session, user_id, nudge_type="missing_income")
    user = await _get_user(session, user_id)

    reply = await handle_nudge_callback(
        user=user, callback_data=f"nudge:{nid}:act",
        db=session, redis=_FakeRedis(),
    )
    assert reply.text == messages_es.NUDGE_ACK_ACT_MISSING_INCOME
    await session.refresh((await session.get(UserNudge, nid)))
    n = await session.get(UserNudge, nid)
    assert n.status == "acted_on"


async def test_callback_act_stale_pending(db_with_user):
    session, user_id = db_with_user
    nid = await _make_nudge(
        session, user_id, nudge_type="stale_pending_confirmation"
    )
    user = await _get_user(session, user_id)

    reply = await handle_nudge_callback(
        user=user, callback_data=f"nudge:{nid}:act",
        db=session, redis=_FakeRedis(),
    )
    assert reply.text == messages_es.NUDGE_ACK_ACT_STALE_PENDING


async def test_callback_act_upcoming_bill(db_with_user):
    session, user_id = db_with_user
    nid = await _make_nudge(
        session, user_id, nudge_type="upcoming_bill",
    )
    user = await _get_user(session, user_id)

    reply = await handle_nudge_callback(
        user=user, callback_data=f"nudge:{nid}:act",
        db=session, redis=_FakeRedis(),
    )
    assert reply.text == messages_es.NUDGE_ACK_ACT_UPCOMING_BILL


# ─────────────────────────────────────────────────────────────────────────────
# dismiss / later
# ─────────────────────────────────────────────────────────────────────────────


async def test_callback_dismiss_soft(db_with_user):
    session, user_id = db_with_user
    nid = await _make_nudge(session, user_id)
    user = await _get_user(session, user_id)

    reply = await handle_nudge_callback(
        user=user, callback_data=f"nudge:{nid}:dismiss",
        db=session, redis=_FakeRedis(),
    )
    assert reply.text == messages_es.NUDGE_ACK_DISMISS_SOFT
    n = await session.get(UserNudge, nid)
    assert n.status == "dismissed"


async def test_callback_dismiss_second_creates_silence_and_hard_reply(db_with_user):
    session, user_id = db_with_user
    now = datetime.now(timezone.utc)
    # Prior dismissed 5d ago
    session.add(
        UserNudge(
            id=uuid.uuid4(),
            user_id=user_id,
            nudge_type="missing_income",
            priority="normal",
            status="dismissed",
            dismissed_at=now - timedelta(days=5),
            dedup_key=f"missing_income:{uuid.uuid4()}",
            payload={},
        )
    )
    await session.commit()
    # Second dismissal
    nid = await _make_nudge(session, user_id, nudge_type="missing_income")
    user = await _get_user(session, user_id)

    reply = await handle_nudge_callback(
        user=user, callback_data=f"nudge:{nid}:dismiss",
        db=session, redis=_FakeRedis(),
    )
    assert reply.text == messages_es.NUDGE_ACK_DISMISS_HARD
    silences = (
        await session.execute(
            select(UserNudgeSilence).where(UserNudgeSilence.user_id == user_id)
        )
    ).scalars().all()
    assert len(silences) == 1
    assert silences[0].nudge_type == "missing_income"


async def test_callback_later_is_always_neutral(db_with_user):
    """later uses NUDGE_ACK_LATER regardless of whether silence triggered."""
    session, user_id = db_with_user
    now = datetime.now(timezone.utc)
    # Prior dismiss exists → threshold would fire
    session.add(
        UserNudge(
            id=uuid.uuid4(),
            user_id=user_id,
            nudge_type="missing_income",
            priority="normal",
            status="dismissed",
            dismissed_at=now - timedelta(days=3),
            dedup_key=f"missing_income:{uuid.uuid4()}",
            payload={},
        )
    )
    await session.commit()
    nid = await _make_nudge(session, user_id, nudge_type="missing_income")
    user = await _get_user(session, user_id)

    reply = await handle_nudge_callback(
        user=user, callback_data=f"nudge:{nid}:later",
        db=session, redis=_FakeRedis(),
    )
    # Later is UX-neutral; silence row gets inserted under the hood but
    # the reply stays friendly rather than "no te molesto más".
    assert reply.text == messages_es.NUDGE_ACK_LATER
    n = await session.get(UserNudge, nid)
    assert n.status == "dismissed"


async def test_callback_dismiss_stale_pending_closes_linked_row(db_with_user):
    """Dismissing a stale_pending nudge also closes the pending_confirmations
    row it points at, so the next evaluator pass won't re-nudge."""
    session, user_id = db_with_user

    # Seed a pending_confirmation that IS stale.
    pcid = uuid.uuid4()
    session.add(
        PendingConfirmation(
            id=pcid,
            user_id=user_id,
            short_id="stalexyz",
            channel="telegram",
            action_type="log_expense",
            proposed_action={"summary_es": "gasto viejo"},
            created_at=datetime.now(timezone.utc) - timedelta(hours=60),
        )
    )
    await session.commit()

    nid = await _make_nudge(
        session, user_id,
        nudge_type="stale_pending_confirmation",
        payload={"pending_confirmation_id": str(pcid)},
    )
    user = await _get_user(session, user_id)

    reply = await handle_nudge_callback(
        user=user, callback_data=f"nudge:{nid}:dismiss",
        db=session, redis=_FakeRedis(),
    )
    assert reply.text in (
        messages_es.NUDGE_ACK_DISMISS_SOFT,
        messages_es.NUDGE_ACK_DISMISS_HARD,
    )

    pc = await session.get(PendingConfirmation, pcid)
    assert pc.resolved_at is not None
    assert pc.resolution == "rejected"


# ─────────────────────────────────────────────────────────────────────────────
# invalid / stale
# ─────────────────────────────────────────────────────────────────────────────


async def test_callback_malformed_returns_expired(db_with_user):
    session, user_id = db_with_user
    user = await _get_user(session, user_id)

    reply = await handle_nudge_callback(
        user=user, callback_data="nudge:not-a-uuid:act",
        db=session, redis=_FakeRedis(),
    )
    assert reply.text == messages_es.NUDGE_EXPIRED


async def test_callback_wrong_prefix_returns_expired(db_with_user):
    session, user_id = db_with_user
    user = await _get_user(session, user_id)

    reply = await handle_nudge_callback(
        user=user, callback_data="pending:abc:yes",  # not a nudge callback
        db=session, redis=_FakeRedis(),
    )
    assert reply.text == messages_es.NUDGE_EXPIRED


async def test_callback_other_user_returns_expired(db_with_user):
    session, user_id = db_with_user
    nid = await _make_nudge(session, user_id)
    # Fabricate a different user just for the callback
    import secrets as _secrets
    other = User(
        id=uuid.uuid4(),
        email=f"other-{uuid.uuid4().hex}@example.com",
        full_name="Other",
        shortcut_token=_secrets.token_urlsafe(48),
    )
    session.add(other)
    await session.commit()
    await session.refresh(other)
    other_id = other.id

    try:
        reply = await handle_nudge_callback(
            user=other, callback_data=f"nudge:{nid}:act",
            db=session, redis=_FakeRedis(),
        )
        assert reply.text == messages_es.NUDGE_EXPIRED
        # The original nudge is NOT mutated.
        n = await session.get(UserNudge, nid)
        assert n.status == "pending"
    finally:
        from sqlalchemy import text
        await session.execute(
            text("DELETE FROM users WHERE id = :u"), {"u": other_id}
        )
        await session.commit()
