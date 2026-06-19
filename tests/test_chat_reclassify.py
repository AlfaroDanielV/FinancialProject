"""Reclassify a movement gasto ↔ ingreso from the chat (typed phrase).

A deterministic, LLM-free corrective phrase ("eso fue un ingreso") flips the
SIGN of the LAST committed movement after a Sí/No confirm. Covers:
- the recognizer (`_reclassify_target`) — tight enough not to hijack a new
  capture or a query;
- the full flow: capture expense → "no, eso fue un ingreso" → confirm → the row
  is now income (amount > 0);
- refusals: "already that kind" and "no recent movement".

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
from api.services.transactions import (
    RECLASSIFY_ALREADY,
    RECLASSIFY_NOT_FOUND,
    RECLASSIFY_REASON_ES,
)
from bot import messages_es
from bot.pipeline import _reclassify_target


def test_recognizer_matches_corrections_and_ignores_captures_and_queries():
    assert _reclassify_target("eso fue un ingreso") is True
    assert _reclassify_target("No, era un ingreso") is True
    assert _reclassify_target("cambialo a ingreso") is True
    assert _reclassify_target("el último fue un gasto") is False
    assert _reclassify_target("reclasificalo como gasto") is False
    # Must NOT fire on a new capture (has an amount) or a query.
    assert _reclassify_target("ingresé 5000") is None
    assert _reclassify_target("gasté 8000 en el super") is None
    assert _reclassify_target("¿cuál fue mi último ingreso?") is None
    # Ambiguous bare negation falls through to the LLM.
    assert _reclassify_target("no era un gasto") is None


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
    )


async def _setup(session, user_id, *, fixture):
    from bot.app import set_llm_client

    session.add(
        Account(
            user_id=user_id,
            name="BAC",
            account_type="checking",
            currency="CRC",
            initial_balance=0,
            is_active=True,
        )
    )
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
async def test_chat_reclassify_expense_to_income_flow(db_with_user):
    session, user_id = db_with_user
    from api.services.llm_extractor import FixtureLLMClient
    from tests.fixtures.extractor_responses import BASIC_EXPENSE_CRC

    token = await _setup(
        session, user_id, fixture=FixtureLLMClient(default=BASIC_EXPENSE_CRC)
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Capture the expense (propose → confirm).
            await _chat(ac, "gasté 5000 colones en el super", token)
            await _chat(ac, messages_es.CONFIRM_BUTTONS_YES, token)

            # Corrective phrase → reclassify proposal (Sí/No, no open_screen).
            propose = await _chat(ac, "no, eso fue un ingreso", token)
            assert propose.status_code == 200, propose.text
            body = propose.json()
            assert body["open_screen"] is None
            assert "INGRESO" in body["reply_text"]
            assert len(body["buttons"]) == 2  # Sí / No, no Editar

            # Confirm → the row flips to income.
            confirm = await _chat(ac, messages_es.CONFIRM_BUTTONS_YES, token)
            assert confirm.status_code == 200, confirm.text
            assert (
                confirm.json()["reply_text"] == messages_es.RECLASSIFIED_TO_INCOME
            )

        session.expire_all()
        rows = (
            await session.execute(
                select(Transaction).where(Transaction.user_id == user_id)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].amount > 0  # was -5000, now +5000
        assert rows[0].envelope_id is None
    finally:
        _clear_db_override()


@pytest.mark.asyncio
async def test_chat_reclassify_already_that_kind_is_refused(db_with_user):
    session, user_id = db_with_user
    from api.services.llm_extractor import FixtureLLMClient
    from tests.fixtures.extractor_responses import BASIC_INCOME_CRC

    token = await _setup(
        session, user_id, fixture=FixtureLLMClient(default=BASIC_INCOME_CRC)
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await _chat(ac, "me pagaron 100000 colones", token)
            await _chat(ac, messages_es.CONFIRM_BUTTONS_YES, token)

            # It's already income — refuse, no proposal.
            resp = await _chat(ac, "eso fue un ingreso", token)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["reply_text"] == RECLASSIFY_REASON_ES[RECLASSIFY_ALREADY]
            assert body["buttons"] == []
    finally:
        _clear_db_override()


@pytest.mark.asyncio
async def test_chat_reclassify_without_recent_movement_is_refused(db_with_user):
    session, user_id = db_with_user
    from api.services.llm_extractor import FixtureLLMClient
    from tests.fixtures.extractor_responses import BASIC_EXPENSE_CRC

    token = await _setup(
        session, user_id, fixture=FixtureLLMClient(default=BASIC_EXPENSE_CRC)
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await _chat(ac, "eso fue un gasto", token)
            assert resp.status_code == 200, resp.text
            assert (
                resp.json()["reply_text"]
                == RECLASSIFY_REASON_ES[RECLASSIFY_NOT_FOUND]
            )
    finally:
        _clear_db_override()
