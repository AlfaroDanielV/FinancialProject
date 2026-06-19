"""Query tool — get_envelope_spending.

The assistant answers "¿cuánto gasté en <sobre>?" by reusing the live
envelope summary. These tests drive the tool function directly (the LLM
routing is separate) against a real seeded envelope + expenses.
"""
from __future__ import annotations

from datetime import date

import pytest

from api.models.envelope import Envelope
from api.models.transaction import Transaction
from app.queries.tools.envelopes import get_envelope_spending


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _patch_session(monkeypatch, session):
    monkeypatch.setattr(
        "app.queries.tools.envelopes.AsyncSessionLocal",
        lambda: _SessionContext(session),
    )


def _this_month(day: int = 15) -> date:
    today = date.today()
    return date(today.year, today.month, day)


async def _seed(session, user_id, *, name="Restaurantes", limit="50000"):
    env = Envelope(
        user_id=user_id,
        name=name,
        envelope_class="wants",
        limit_amount=limit,
        currency="CRC",
    )
    session.add(env)
    await session.commit()
    await session.refresh(env)
    return env


@pytest.mark.asyncio
async def test_named_envelope_returns_spend(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    env = await _seed(session, user_id, name="Restaurantes", limit="50000")
    session.add_all(
        [
            Transaction(
                user_id=user_id,
                amount=-12000,
                currency="CRC",
                transaction_date=_this_month(),
                source="manual",
                status="confirmed",
                envelope_id=env.id,
            ),
            Transaction(
                user_id=user_id,
                amount=-8000,
                currency="CRC",
                transaction_date=_this_month(),
                source="manual",
                status="confirmed",
                envelope_id=env.id,
            ),
        ]
    )
    await session.commit()

    # Case-insensitive / partial match still resolves the envelope.
    result = await get_envelope_spending(envelope_name="restaurante", user_id=user_id)
    assert result["matched_count"] == 1
    item = result["envelopes"][0]
    assert item["name"] == "Restaurantes"
    assert item["spent"] == "20000.00"
    assert item["remaining"] == "30000.00"
    assert item["over_limit"] is False


@pytest.mark.asyncio
async def test_unknown_name_lists_available(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _seed(session, user_id, name="Súper")

    result = await get_envelope_spending(envelope_name="gasolina", user_id=user_id)
    assert result["matched_count"] == 0
    assert "Súper" in result["available_envelope_names"]


@pytest.mark.asyncio
async def test_no_name_returns_all(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _seed(session, user_id, name="Súper")
    await _seed(session, user_id, name="Ocio")

    result = await get_envelope_spending(user_id=user_id)
    assert result["matched_count"] == 2
    assert {e["name"] for e in result["envelopes"]} == {"Súper", "Ocio"}
