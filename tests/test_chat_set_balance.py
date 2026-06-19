"""Chat re-anchor — "corregí mi saldo" (A7 chat).

The LLM extracts the real balance (`amount`) + account; the DETERMINISTIC
dispatcher proposes; on "Sí" `apply_anchor` appends the anchor + the
informational ajuste and the balance heals to the stated value in one step.
The LLM never decides the balance (reinforces "LLM extracts; rules decide").
"""
from __future__ import annotations

import socket
from datetime import date
from decimal import Decimal
from urllib.parse import urlparse

import pytest
from httpx import ASGITransport, AsyncClient

from api.config import settings
from api.database import get_db
from api.main import app
from api.models.account import Account
from api.models.transaction import Transaction
from api.models.user import User
from api.services.accounts import compute_account_balances
from api.services.auth.magic_link import generate_link
from api.services.llm_extractor import ExtractionResult, Intent
from api.services.telegram_dispatcher import (
    AskClarification,
    ProposeAction,
    dispatch,
)
from tests.fixtures.extractor_responses import RecordedLLMResponse


def _db_reachable() -> bool:
    try:
        url = urlparse(settings.database_url.replace("+asyncpg", ""))
        with socket.create_connection(
            (url.hostname or "localhost", url.port or 5432), timeout=0.5
        ):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="Postgres not reachable"
)


SET_BALANCE_BAC = RecordedLLMResponse(
    tool_input={
        "intent": "set_balance",
        "dispatcher": "write",
        "amount": 90000,
        "currency": None,
        "merchant": None,
        "category_hint": None,
        "account_hint": "BAC",
        "occurred_at_hint": None,
        "query_window": None,
        "confidence": 0.95,
        "raw_notes": None,
    },
    input_tokens=430,
    output_tokens=40,
    cache_read_input_tokens=380,
)


def _override_db(session):
    async def _yield():
        yield session

    app.dependency_overrides[get_db] = _yield


def _clear_db_override():
    app.dependency_overrides.pop(get_db, None)


async def _account(session, user_id, name, *, account_type="checking", initial="0"):
    a = Account(
        user_id=user_id,
        name=name,
        account_type=account_type,
        currency="CRC",
        initial_balance=Decimal(initial),
    )
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return a


async def _txn(session, user_id, account_id, amount, d):
    session.add(
        Transaction(
            user_id=user_id,
            account_id=account_id,
            amount=Decimal(amount),
            currency="CRC",
            merchant="seed",
            transaction_date=d,
            source="manual",
            status="confirmed",
        )
    )
    await session.commit()


def _ext(**overrides) -> ExtractionResult:
    base = dict(
        intent=Intent.SET_BALANCE,
        dispatcher="write",
        amount=Decimal("90000"),
        account_hint="BAC",
        confidence=0.95,
    )
    base.update(overrides)
    return ExtractionResult(**base)


async def _chat(client: AsyncClient, text: str, bearer: str):
    return await client.post(
        "/api/v1/chat/message",
        json={"text": text},
        headers={"Authorization": f"Bearer {bearer}"},
    )


# ── direct dispatch ──────────────────────────────────────────────────────────


async def test_dispatch_proposes_set_balance(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    a = await _account(session, user_id, "BAC")
    res = await dispatch(
        extraction=_ext(), user=user, today=date.today(), db=session
    )
    assert isinstance(res, ProposeAction)
    assert res.action_type == "set_balance"
    assert res.payload["account_id"] == str(a.id)
    assert res.payload["value"] == "90000"
    assert res.payload["currency"] == "CRC"


async def test_dispatch_missing_amount_clarifies(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _account(session, user_id, "BAC")
    res = await dispatch(
        extraction=_ext(amount=None), user=user, today=date.today(), db=session
    )
    assert isinstance(res, AskClarification)
    assert res.awaiting_field == "amount"


async def test_dispatch_credit_account_redirects(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _account(session, user_id, "Visa BAC", account_type="credit")
    res = await dispatch(
        extraction=_ext(account_hint="Visa BAC"),
        user=user,
        today=date.today(),
        db=session,
    )
    assert isinstance(res, AskClarification)
    assert res.awaiting_field == "account"
    assert "tarjeta" in res.question_es.lower()


async def test_dispatch_missing_account_asks_with_options(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    await _account(session, user_id, "BAC")
    await _account(session, user_id, "BCR")
    res = await dispatch(
        extraction=_ext(account_hint=None),
        user=user,
        today=date.today(),
        db=session,
    )
    assert isinstance(res, AskClarification)
    assert res.awaiting_field == "account"
    assert set(res.options) >= {"BAC", "BCR"}


# ── E2E: propose → sí → heal in one step ───────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_set_balance_heals_in_one_step(db_with_user):
    from api.services.llm_extractor import FixtureLLMClient
    from bot.app import set_llm_client

    session, user_id = db_with_user
    a = await _account(session, user_id, "BAC", initial="100000")
    await _txn(session, user_id, a.id, "-25000", date.today())  # projected 75000

    set_llm_client(FixtureLLMClient(default=SET_BALANCE_BAC))
    link = await generate_link(session, user_id=user_id, purpose="onboarding")
    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            ex = await ac.post(
                "/api/v1/auth/magic-link/exchange",
                json={"token": link.raw_token},
            )
            token = ex.json()["token"]
            propose = await _chat(ac, "corregí mi saldo del BAC a 90000", token)
            assert propose.status_code == 200, propose.text
            assert len(propose.json()["buttons"]) == 3  # Sí / No / Editar

            confirm = await _chat(ac, "sí", token)
            assert confirm.status_code == 200, confirm.text
            assert "BAC" in confirm.json()["reply_text"]

        bal = await compute_account_balances(
            session, user_id=user_id, account_ids=[a.id]
        )
        assert bal[a.id].current == Decimal("90000")  # healed in one step
    finally:
        _clear_db_override()
