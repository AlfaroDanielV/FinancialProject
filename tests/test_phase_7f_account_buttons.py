"""Phase 7f B1 — account selection buttons on clarification questions.

When the write dispatcher asks "¿De qué cuenta?" (or a transfer side), the
active account names ride along as `AskClarification.options` and surface as
tappable buttons on both channels:

- Native chat: chips post the LABEL back as text → the existing
  clarification merge path resolves it (no mobile changes).
- Telegram: inline buttons carry `clarify:{nonce}:{idx}`;
  `handle_clarify_callback` validates the nonce against the stashed
  ClarificationState and routes the label through the same merge path.

Covers: dispatcher option population + cap, the chat E2E tap-as-text round
trip, and the Telegram callback (valid / stale-nonce / bad-index / expired).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.database import get_db
from api.main import app
from api.models.account import Account
from api.models.transaction import Transaction
from api.models.user import User
from api.services import telegram_dispatcher as td
from api.services.auth.magic_link import generate_link
from api.services.llm_extractor import ExtractionResult, Intent
from bot import messages_es
from bot.clarification import load_clarification
from bot.pipeline import handle_clarify_callback


# ── dispatcher-level fakes (mirrors test_telegram_dispatcher.py) ──────────────


@dataclass
class _FakeAccount:
    name: str
    id: uuid.UUID = None  # type: ignore[assignment]
    account_type: str = "checking"
    currency: str = "CRC"

    def __post_init__(self) -> None:
        if self.id is None:
            self.id = uuid.uuid4()


@dataclass
class _FakeUser:
    id: uuid.UUID
    currency: str = "CRC"


def _stub_accounts(
    monkeypatch, accounts: list[_FakeAccount], resolved: Optional[_FakeAccount]
):
    async def _list_active(user, db):
        return accounts

    async def _resolve_account(user, hint, db):
        return resolved

    monkeypatch.setattr(td, "list_active", _list_active)
    monkeypatch.setattr(td, "resolve_account", _resolve_account)


def _extraction(**overrides) -> ExtractionResult:
    base = {
        "intent": Intent.LOG_EXPENSE,
        "dispatcher": "write",
        "amount": Decimal("5000"),
        "currency": "CRC",
        "merchant": "Super",
        "category_hint": "supermercado",
        "account_hint": None,
        "occurred_at_hint": None,
        "query_window": None,
        "confidence": 0.9,
        "raw_notes": None,
    }
    base.update(overrides)
    return ExtractionResult(**base)


# ── 1. dispatcher fills options ───────────────────────────────────────────────


async def test_expense_account_question_carries_options(monkeypatch):
    accounts = [_FakeAccount(name="BAC"), _FakeAccount(name="Popular")]
    _stub_accounts(monkeypatch, accounts, None)
    result = await td.dispatch(
        extraction=_extraction(),
        user=_FakeUser(id=uuid.uuid4()),
        today=date(2026, 6, 12),
        db=object(),
    )
    assert isinstance(result, td.AskClarification)
    assert result.awaiting_field == "account"
    assert result.options == ["BAC", "Popular"]


async def test_options_capped_at_max(monkeypatch):
    accounts = [_FakeAccount(name=f"Cuenta {i}") for i in range(12)]
    _stub_accounts(monkeypatch, accounts, None)
    result = await td.dispatch(
        extraction=_extraction(),
        user=_FakeUser(id=uuid.uuid4()),
        today=date(2026, 6, 12),
        db=object(),
    )
    assert isinstance(result, td.AskClarification)
    assert len(result.options) == td.MAX_ACCOUNT_OPTIONS


async def test_transfer_side_question_carries_options(monkeypatch):
    accounts = [
        _FakeAccount(name="BAC"),
        _FakeAccount(name="Popular"),
        _FakeAccount(name="Tarjeta BAC", account_type="credit"),
    ]
    _stub_accounts(monkeypatch, accounts, None)
    result = await td.dispatch(
        extraction=_extraction(
            intent=Intent.LOG_TRANSFER,
            transfer_from_hint=None,
            transfer_to_hint=None,
        ),
        user=_FakeUser(id=uuid.uuid4()),
        today=date(2026, 6, 12),
        db=object(),
    )
    assert isinstance(result, td.AskClarification)
    assert result.awaiting_field == "transfer_to"
    assert result.options == ["BAC", "Popular", "Tarjeta BAC"]


# ── chat E2E helpers (mirrors test_phase_6f_b5_chat_write.py) ─────────────────


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


async def _setup_two_accounts(session, user_id) -> str:
    """Two active accounts (so no-hint expenses need clarification) + bearer."""
    from api.services.llm_extractor import FixtureLLMClient
    from bot.app import set_llm_client
    from tests.fixtures.extractor_responses import BASIC_EXPENSE_CRC

    for name in ("BAC", "Popular"):
        session.add(
            Account(
                user_id=user_id,
                name=name,
                account_type="checking",
                currency="CRC",
                initial_balance=0,
                is_active=True,
            )
        )
    await session.commit()

    set_llm_client(FixtureLLMClient(default=BASIC_EXPENSE_CRC))

    link = await generate_link(session, user_id=user_id, purpose="onboarding")
    _override_db(session)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        exchange = await ac.post(
            "/api/v1/auth/magic-link/exchange", json={"token": link.raw_token}
        )
    assert exchange.status_code == 200
    return exchange.json()["token"]


# ── 2. chat E2E: buttons appear; tapping = posting the label as text ──────────


@pytest.mark.asyncio
async def test_chat_account_question_shows_buttons_and_label_reply_resolves(
    db_with_user,
):
    session, user_id = db_with_user
    token = await _setup_two_accounts(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # No account hint + two accounts → clarification with buttons.
            ask = await _chat(ac, "gasté 5000 colones en el super", token)
            assert ask.status_code == 200, ask.text
            body = ask.json()
            labels = [b["label"] for b in body["buttons"]]
            assert labels == ["BAC", "Popular"]
            assert all(
                b["callback_data"].startswith("clarify:")
                for b in body["buttons"]
            )

            # Native chip behavior: post the label as text → proposal.
            answer = await _chat(ac, "Popular", token)
            assert answer.status_code == 200, answer.text
            proposal = answer.json()
            assert len(proposal["buttons"]) == 3  # Sí / No / Editar
            assert "Popular" in proposal["reply_text"]

            confirm = await _chat(ac, "sí", token)
            assert confirm.status_code == 200

        rows = (
            (
                await session.execute(
                    select(Transaction).where(Transaction.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        accounts = {
            a.id: a.name
            for a in (
                (
                    await session.execute(
                        select(Account).where(Account.user_id == user_id)
                    )
                )
                .scalars()
                .all()
            )
        }
        assert accounts[rows[0].account_id] == "Popular"
    finally:
        _clear_db_override()


# ── 3. Telegram callback path ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clarify_callback_valid_tap_proposes(db_with_user):
    session, user_id = db_with_user
    token = await _setup_two_accounts(session, user_id)
    try:
        from api.redis_client import get_redis

        redis = get_redis()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            ask = await _chat(ac, "gasté 5000 colones en el super", token)
            assert ask.status_code == 200

        state = await load_clarification(user_id=user_id, redis=redis)
        assert state is not None and state.nonce and state.options

        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one()
        reply = await handle_clarify_callback(
            user=user,
            callback_data=f"clarify:{state.nonce}:1",  # "Popular"
            db=session,
            redis=redis,
        )
        # The tap resolves into a proposal carrying its own confirm buttons.
        assert len(reply.buttons) == 3
        assert reply.buttons[0].callback_data.startswith("pending:")
        assert "Popular" in reply.text
    finally:
        _clear_db_override()


@pytest.mark.asyncio
async def test_clarify_callback_rejects_stale_or_invalid(db_with_user):
    session, user_id = db_with_user
    token = await _setup_two_accounts(session, user_id)
    try:
        from api.redis_client import get_redis
        from bot.clarification import clear_clarification

        redis = get_redis()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            ask = await _chat(ac, "gasté 5000 colones en el super", token)
            assert ask.status_code == 200

        state = await load_clarification(user_id=user_id, redis=redis)
        assert state is not None
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one()

        # Wrong nonce (tap on a superseded question).
        stale = await handle_clarify_callback(
            user=user,
            callback_data="clarify:ZZZZZZ:0",
            db=session,
            redis=redis,
        )
        assert stale.text == messages_es.CLARIFY_EXPIRED

        # Out-of-range index.
        bad_idx = await handle_clarify_callback(
            user=user,
            callback_data=f"clarify:{state.nonce}:99",
            db=session,
            redis=redis,
        )
        assert bad_idx.text == messages_es.CLARIFY_EXPIRED

        # Expired question (state gone).
        await clear_clarification(user_id=user_id, redis=redis)
        expired = await handle_clarify_callback(
            user=user,
            callback_data=f"clarify:{state.nonce}:0",
            db=session,
            redis=redis,
        )
        assert expired.text == messages_es.CLARIFY_EXPIRED
    finally:
        _clear_db_override()
