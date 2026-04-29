from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.queries.tools.pending import get_pending_confirmations
from api.models.pending_confirmation import PendingConfirmation
from api.models.user import User


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _patch_session(monkeypatch, session):
    monkeypatch.setattr(
        "app.queries.tools.pending.AsyncSessionLocal",
        lambda: _SessionContext(session),
    )


async def _add_pending(
    session,
    user_id,
    *,
    summary,
    action_type="log_expense",
    created_at,
    resolved_at=None,
    short_id="abc123",
):
    p = PendingConfirmation(
        user_id=user_id,
        short_id=short_id,
        channel="telegram",
        action_type=action_type,
        proposed_action={
            "action_type": action_type,
            "summary_es": summary,
            "payload": {"amount": "5000", "merchant": "super"},
        },
        created_at=created_at,
        resolved_at=resolved_at,
        resolution="confirmed" if resolved_at else None,
    )
    session.add(p)
    await session.commit()
    return p


async def _seed_other_user(session) -> uuid.UUID:
    u = User(
        email=f"other-{uuid.uuid4().hex}@example.com",
        full_name="Other",
        shortcut_token=secrets.token_urlsafe(48),
    )
    session.add(u)
    await session.commit()
    await session.refresh(u)
    return u.id


@pytest.mark.asyncio
async def test_get_pending_only_returns_unresolved(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    now = datetime.now(timezone.utc)
    await _add_pending(
        session, user_id,
        summary="Registrar gasto de ₡5000 en supermercado",
        created_at=now - timedelta(hours=2),
    )
    await _add_pending(
        session, user_id,
        summary="Otro ya resuelto",
        created_at=now - timedelta(hours=10),
        resolved_at=now - timedelta(hours=8),
        short_id="zzz",
    )

    result = await get_pending_confirmations(user_id=user_id)
    assert result["total_count"] == 1
    p = result["pending"][0]
    assert "supermercado" in p["proposed_action"]
    assert 1.5 < p["age_hours"] < 2.5


@pytest.mark.asyncio
async def test_get_pending_age_hours_decimal(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    now = datetime.now(timezone.utc)
    await _add_pending(
        session, user_id,
        summary="Reciente",
        created_at=now - timedelta(minutes=30),
    )
    result = await get_pending_confirmations(user_id=user_id)
    age = result["pending"][0]["age_hours"]
    assert 0.4 < age < 0.6


@pytest.mark.asyncio
async def test_get_pending_user_isolation(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    other_uid = await _seed_other_user(session)
    try:
        now = datetime.now(timezone.utc)
        await _add_pending(
            session, user_id, summary="Mía", created_at=now - timedelta(hours=1),
        )
        await _add_pending(
            session, other_uid, summary="No mostrar",
            created_at=now - timedelta(hours=1), short_id="other",
        )

        result = await get_pending_confirmations(user_id=user_id)
        assert result["total_count"] == 1
        assert result["pending"][0]["proposed_action"] == "Mía"
    finally:
        for stmt in (
            "DELETE FROM pending_confirmations WHERE user_id = :u",
            "DELETE FROM users WHERE id = :u",
        ):
            await session.execute(text(stmt), {"u": other_uid})
        await session.commit()
