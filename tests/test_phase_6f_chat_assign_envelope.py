"""Envelope budgeting (Sobres) — post-capture assignment hint in the chat.

After an EXPENSE commits through the chat, the BotReply must carry an
`open_screen` hint (`screen='assign_envelope'`) with the new transaction id in
`prefill`, so the native chat can offer an in-chat "Asignar a un sobre"
affordance. Income commits must NOT carry the hint (envelopes are spending
caps only).

Reuses the B5 chat-write harness (FixtureLLMClient + magic-link bearer).
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.database import get_db
from api.main import app
from api.models.account import Account
from api.models.transaction import Transaction
from api.services.auth.magic_link import generate_link
from bot import messages_es


def _override_db(session):
    async def _yield():
        yield session

    app.dependency_overrides[get_db] = _yield


def _clear_db_override():
    app.dependency_overrides.pop(get_db, None)


async def _chat(client: AsyncClient, text: str, bearer: str):
    return await client.post(
        "/api/v1/chat/message",
        json={"text": text},
        headers={"Authorization": f"Bearer {bearer}"},
        cookies={},
    )


async def _setup(session, user_id, *, fixture):
    from bot.app import set_llm_client

    acct = Account(
        user_id=user_id,
        name="BAC",
        account_type="checking",
        currency="CRC",
        initial_balance=0,
        is_active=True,
    )
    session.add(acct)
    await session.commit()

    set_llm_client(fixture)

    link = await generate_link(session, user_id=user_id, purpose="onboarding")
    _override_db(session)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        exchange = await ac.post(
            "/api/v1/auth/magic-link/exchange", json={"token": link.raw_token}
        )
    assert exchange.status_code == 200
    return exchange.json()["token"]


@pytest.mark.asyncio
async def test_committed_expense_carries_assign_envelope_hint(db_with_user):
    session, user_id = db_with_user
    from api.services.llm_extractor import FixtureLLMClient
    from tests.fixtures.extractor_responses import BASIC_EXPENSE_CRC

    token = await _setup(
        session, user_id, fixture=FixtureLLMClient(default=BASIC_EXPENSE_CRC)
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            propose = await _chat(ac, "gasté 5000 colones en el super", token)
            assert propose.status_code == 200, propose.text
            assert propose.json()["open_screen"] is None  # proposal has no hint

            confirm = await _chat(ac, messages_es.CONFIRM_BUTTONS_YES, token)
            assert confirm.status_code == 200, confirm.text
            body = confirm.json()

        open_screen = body["open_screen"]
        assert open_screen is not None
        assert open_screen["screen"] == "assign_envelope"

        rows = (
            await session.execute(
                select(Transaction).where(Transaction.user_id == user_id)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert open_screen["prefill"]["transaction_id"] == str(rows[0].id)
        assert open_screen["prefill"]["currency"] == "CRC"
    finally:
        _clear_db_override()


@pytest.mark.asyncio
async def test_committed_income_carries_reclassify_hint_not_envelope(db_with_user):
    """Income never gets an `assign_envelope` hint (caps are spending-only), but
    it DOES carry a `reclassify` hint so the chat can offer an "Era un gasto"
    chip on the just-created row."""
    session, user_id = db_with_user
    from api.services.llm_extractor import FixtureLLMClient
    from tests.fixtures.extractor_responses import BASIC_INCOME_CRC

    token = await _setup(
        session, user_id, fixture=FixtureLLMClient(default=BASIC_INCOME_CRC)
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            propose = await _chat(ac, "me pagaron 100000 colones", token)
            assert propose.status_code == 200, propose.text
            confirm = await _chat(ac, messages_es.CONFIRM_BUTTONS_YES, token)
            assert confirm.status_code == 200, confirm.text
            body = confirm.json()

        open_screen = body["open_screen"]
        assert open_screen is not None
        assert open_screen["screen"] == "reclassify"

        rows = (
            await session.execute(
                select(Transaction).where(Transaction.user_id == user_id)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert open_screen["prefill"]["transaction_id"] == str(rows[0].id)
        assert open_screen["prefill"]["currency"] == "CRC"
    finally:
        _clear_db_override()
