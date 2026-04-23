"""Tests for the state-transition service used by both the REST router
and the Telegram dispatcher integration.

Focus: the silence threshold math, since that's the only branch with
meaningful logic. Plain status flips are trivial but covered for
regression safety.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from fastapi import HTTPException

from api.models.user_nudge import UserNudge, UserNudgeSilence
from api.services.nudges.actions import mark_acted_on, mark_dismissed
from api.services.nudges.policy import (
    REASON_AUTO_DISMISSED_2X,
    SILENCE_DURATION_DAYS,
)


async def _insert_nudge(
    session,
    user_id: uuid.UUID,
    *,
    nudge_type: str = "missing_income",
    status: str = "pending",
    dismissed_at: datetime | None = None,
) -> uuid.UUID:
    nid = uuid.uuid4()
    session.add(
        UserNudge(
            id=nid,
            user_id=user_id,
            nudge_type=nudge_type,
            priority="normal",
            status=status,
            dedup_key=f"{nudge_type}:{uuid.uuid4()}",
            payload={},
            dismissed_at=dismissed_at,
        )
    )
    await session.commit()
    return nid


async def _silences_for(session, user_id: uuid.UUID) -> list[UserNudgeSilence]:
    result = await session.execute(
        select(UserNudgeSilence).where(UserNudgeSilence.user_id == user_id)
    )
    return list(result.scalars().all())


# ─────────────────────────────────────────────────────────────────────────────
# mark_dismissed
# ─────────────────────────────────────────────────────────────────────────────


async def test_dismiss_flips_status(db_with_user):
    session, user_id = db_with_user
    nid = await _insert_nudge(session, user_id)
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)

    outcome = await mark_dismissed(
        session, user_id=user_id, nudge_id=nid, now=now
    )
    await session.commit()

    assert outcome.nudge.status == "dismissed"
    assert outcome.nudge.dismissed_at == now
    assert outcome.silence_created is False
    assert await _silences_for(session, user_id) == []


async def test_dismiss_second_within_30d_creates_silence(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    # One prior dismissed nudge of the same type, 20 days ago (inside 30d).
    prior_id = await _insert_nudge(
        session, user_id,
        status="dismissed",
        dismissed_at=now - timedelta(days=20),
    )
    # Fresh pending nudge we're about to dismiss (2nd).
    second_id = await _insert_nudge(session, user_id)

    outcome = await mark_dismissed(
        session, user_id=user_id, nudge_id=second_id, now=now
    )
    await session.commit()

    assert outcome.silence_created is True
    silences = await _silences_for(session, user_id)
    assert len(silences) == 1
    silence = silences[0]
    assert silence.nudge_type == "missing_income"
    assert silence.reason == REASON_AUTO_DISMISSED_2X
    # silenced_until ≈ now + 14 days
    expected = now + timedelta(days=SILENCE_DURATION_DAYS)
    assert abs((silence.silenced_until - expected).total_seconds()) < 2


async def test_dismiss_third_in_30d_does_not_duplicate_silence(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    # Two prior dismissed nudges inside 30d → already has an active silence.
    await _insert_nudge(
        session, user_id,
        status="dismissed", dismissed_at=now - timedelta(days=25),
    )
    await _insert_nudge(
        session, user_id,
        status="dismissed", dismissed_at=now - timedelta(days=5),
    )
    session.add(
        UserNudgeSilence(
            user_id=user_id,
            nudge_type="missing_income",
            silenced_until=now + timedelta(days=9),
            reason=REASON_AUTO_DISMISSED_2X,
        )
    )
    await session.commit()

    # The third dismissal must not add a second silence row.
    third_id = await _insert_nudge(session, user_id)
    outcome = await mark_dismissed(
        session, user_id=user_id, nudge_id=third_id, now=now
    )
    await session.commit()

    assert outcome.silence_created is False
    silences = await _silences_for(session, user_id)
    assert len(silences) == 1


async def test_dismiss_second_outside_30d_does_not_silence(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    # Prior dismiss is 45 days ago — OUTSIDE the lookback.
    await _insert_nudge(
        session, user_id,
        status="dismissed", dismissed_at=now - timedelta(days=45),
    )
    second_id = await _insert_nudge(session, user_id)

    outcome = await mark_dismissed(
        session, user_id=user_id, nudge_id=second_id, now=now
    )
    await session.commit()

    assert outcome.silence_created is False
    assert await _silences_for(session, user_id) == []


async def test_dismiss_different_types_do_not_combine(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
    await _insert_nudge(
        session, user_id,
        nudge_type="missing_income",
        status="dismissed", dismissed_at=now - timedelta(days=10),
    )
    # Dismiss an upcoming_bill — different type; should NOT trip the silence.
    bill_id = await _insert_nudge(
        session, user_id, nudge_type="upcoming_bill",
    )
    outcome = await mark_dismissed(
        session, user_id=user_id, nudge_id=bill_id, now=now
    )
    await session.commit()

    assert outcome.silence_created is False
    assert await _silences_for(session, user_id) == []


async def test_dismiss_wrong_user_404(db_with_user):
    session, user_id = db_with_user
    nid = await _insert_nudge(session, user_id)
    other_user = uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        await mark_dismissed(
            session, user_id=other_user, nudge_id=nid,
        )
    assert exc.value.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# mark_acted_on
# ─────────────────────────────────────────────────────────────────────────────


async def test_act_flips_status(db_with_user):
    session, user_id = db_with_user
    nid = await _insert_nudge(session, user_id)
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)

    nudge = await mark_acted_on(
        session, user_id=user_id, nudge_id=nid, now=now
    )
    await session.commit()

    assert nudge.status == "acted_on"
    assert nudge.acted_on_at == now


async def test_act_idempotent(db_with_user):
    session, user_id = db_with_user
    nid = await _insert_nudge(session, user_id)
    now = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)

    n1 = await mark_acted_on(session, user_id=user_id, nudge_id=nid, now=now)
    await session.commit()
    first_ts = n1.acted_on_at

    n2 = await mark_acted_on(
        session, user_id=user_id, nudge_id=nid,
        now=now + timedelta(hours=2),
    )
    await session.commit()
    # Second call does not re-stamp acted_on_at — it's already acted_on.
    assert n2.acted_on_at == first_ts


async def test_act_wrong_user_404(db_with_user):
    session, user_id = db_with_user
    nid = await _insert_nudge(session, user_id)
    other_user = uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        await mark_acted_on(
            session, user_id=other_user, nudge_id=nid,
        )
    assert exc.value.status_code == 404
