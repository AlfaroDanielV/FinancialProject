"""Phase 7b B1 — transfers between own accounts through the chat pipeline.

A credit-card payment IS a transfer (source account → credit account), never
an expense: "pagué la tarjeta" used to extract as log_expense, double-counting
spending and pushing the card balance the wrong way. The deterministic path
(dispatch → clarify → propose → confirm → commit through the shared transfers
service) is covered without the real LLM via FixtureLLMClient and direct
dispatch() calls.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.database import get_db
from api.main import app
from api.models.account import Account
from api.models.transaction import Transaction
from api.models.transfer import Transfer
from api.models.user import User
from api.services.auth.magic_link import generate_link
from api.services.llm_extractor import ExtractionResult, Intent
from api.services.telegram_dispatcher import (
    AskClarification,
    ProposeAction,
    Reject,
    dispatch,
)
from bot.clarification import ClarificationState, merge_reply
from bot.undo import run_undo


# ── helpers ───────────────────────────────────────────────────────────────────


def _override_db(session):
    async def _yield():
        yield session

    app.dependency_overrides[get_db] = _yield


def _clear_db_override():
    app.dependency_overrides.pop(get_db, None)


async def _add_account(
    session,
    user_id,
    name: str,
    *,
    account_type: str = "checking",
    currency: str = "CRC",
) -> Account:
    account = Account(
        user_id=user_id,
        name=name,
        account_type=account_type,
        currency=currency,
        initial_balance=Decimal("0"),
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


def _transfer_extraction(**overrides) -> ExtractionResult:
    base = dict(
        intent=Intent.LOG_TRANSFER,
        dispatcher="write",
        amount=Decimal("80000"),
        transfer_to_hint="tarjeta",
        confidence=0.92,
    )
    base.update(overrides)
    return ExtractionResult(**base)


async def _get_user(session, user_id) -> User:
    return (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one()


# ── dispatcher-level coverage ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_card_payment_proposes_transfer_with_defaulted_source(db_with_user):
    """Two accounts, to-hint='tarjeta' → the single credit account; the source
    defaults to the other account and the summary says so + uses card copy."""
    session, user_id = db_with_user
    corriente = await _add_account(session, user_id, "Corriente BCR")
    visa = await _add_account(
        session, user_id, "BAC Visa", account_type="credit"
    )
    user = await _get_user(session, user_id)

    decision = await dispatch(
        extraction=_transfer_extraction(),
        user=user,
        today=__import__("datetime").date.today(),
        db=session,
    )
    assert isinstance(decision, ProposeAction), decision
    assert decision.action_type == "log_transfer"
    assert decision.payload["from_account_id"] == str(corriente.id)
    assert decision.payload["to_account_id"] == str(visa.id)
    assert decision.payload["is_card_payment"] is True
    assert "Pago de tarjeta" in decision.summary_es
    assert "Asumí" in decision.summary_es


@pytest.mark.asyncio
async def test_transfer_clarifies_source_then_merges_reply(db_with_user):
    """Three accounts → the missing source can't default; the dispatcher asks
    listing options and merge_reply folds the answer back in."""
    session, user_id = db_with_user
    await _add_account(session, user_id, "Corriente BCR")
    ahorros = await _add_account(session, user_id, "Ahorros BN")
    await _add_account(session, user_id, "BAC Visa", account_type="credit")
    user = await _get_user(session, user_id)
    today = __import__("datetime").date.today()

    decision = await dispatch(
        extraction=_transfer_extraction(), user=user, today=today, db=session
    )
    assert isinstance(decision, AskClarification), decision
    assert decision.awaiting_field == "transfer_from"
    # Phase 7f: account names ride as tappable options, not in the question.
    assert "Ahorros BN" in decision.options

    state = ClarificationState(
        partial=decision.partial,
        awaiting_field=decision.awaiting_field,
        question_es=decision.question_es,
    )
    merged = merge_reply(state, "Ahorros BN", user)
    assert merged is not None
    assert merged.transfer_from_hint == "Ahorros BN"

    second = await dispatch(
        extraction=merged, user=user, today=today, db=session
    )
    assert isinstance(second, ProposeAction), second
    assert second.payload["from_account_id"] == str(ahorros.id)
    assert second.payload["is_card_payment"] is True


@pytest.mark.asyncio
async def test_transfer_missing_amount_clarifies(db_with_user):
    session, user_id = db_with_user
    await _add_account(session, user_id, "Corriente BCR")
    await _add_account(session, user_id, "BAC Visa", account_type="credit")
    user = await _get_user(session, user_id)

    decision = await dispatch(
        extraction=_transfer_extraction(amount=None),
        user=user,
        today=__import__("datetime").date.today(),
        db=session,
    )
    assert isinstance(decision, AskClarification), decision
    assert decision.awaiting_field == "amount"


@pytest.mark.asyncio
async def test_transfer_cross_currency_rejected_in_chat(db_with_user):
    """CRC source + USD credit card → chat rejects and points at the native
    modal (fx stays out of the deterministic chat write path, v1)."""
    session, user_id = db_with_user
    await _add_account(session, user_id, "Corriente BCR")
    await _add_account(
        session, user_id, "Visa USD", account_type="credit", currency="USD"
    )
    user = await _get_user(session, user_id)

    decision = await dispatch(
        extraction=_transfer_extraction(),
        user=user,
        today=__import__("datetime").date.today(),
        db=session,
    )
    assert isinstance(decision, Reject), decision
    assert decision.reason_code == "transfer_cross_currency"
    assert "monedas" in decision.message_es


@pytest.mark.asyncio
async def test_transfer_same_account_reasks(db_with_user):
    session, user_id = db_with_user
    await _add_account(session, user_id, "Corriente BCR")
    await _add_account(session, user_id, "BAC Visa", account_type="credit")
    user = await _get_user(session, user_id)

    decision = await dispatch(
        extraction=_transfer_extraction(
            transfer_from_hint="Corriente BCR", transfer_to_hint="Corriente BCR"
        ),
        user=user,
        today=__import__("datetime").date.today(),
        db=session,
    )
    assert isinstance(decision, AskClarification), decision
    assert decision.awaiting_field == "transfer_from"
    assert "misma" in decision.question_es


@pytest.mark.asyncio
async def test_transfer_needs_two_accounts(db_with_user):
    session, user_id = db_with_user
    await _add_account(session, user_id, "Corriente BCR")
    user = await _get_user(session, user_id)

    decision = await dispatch(
        extraction=_transfer_extraction(),
        user=user,
        today=__import__("datetime").date.today(),
        db=session,
    )
    assert isinstance(decision, Reject), decision
    assert decision.reason_code == "transfer_needs_two_accounts"


# ── full flow: chat → propose → confirm → rows; undo removes all three ────────


@pytest.mark.asyncio
async def test_card_payment_full_flow_commits_and_undoes(db_with_user):
    from tests.fixtures.extractor_responses import LOG_TRANSFER_CARD
    from bot.app import set_llm_client
    from api.services.llm_extractor import FixtureLLMClient
    from api.redis_client import get_redis

    session, user_id = db_with_user
    corriente = await _add_account(session, user_id, "Corriente BCR")
    visa = await _add_account(
        session, user_id, "BAC Visa", account_type="credit"
    )

    set_llm_client(FixtureLLMClient(default=LOG_TRANSFER_CARD))
    link = await generate_link(session, user_id=user_id, purpose="onboarding")
    _override_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            exchange = await ac.post(
                "/api/v1/auth/magic-link/exchange", json={"token": link.raw_token}
            )
            assert exchange.status_code == 200, exchange.text
            token = exchange.json()["token"]
            headers = {"Authorization": f"Bearer {token}"}

            propose = await ac.post(
                "/api/v1/chat/message",
                json={"text": "pagué la tarjeta, 80 mil"},
                headers=headers,
            )
            assert propose.status_code == 200, propose.text
            assert "Pago de tarjeta" in propose.json()["reply_text"]

            confirm = await ac.post(
                "/api/v1/chat/message", json={"text": "sí"}, headers=headers
            )
            assert confirm.status_code == 200, confirm.text
            assert "pago de tu tarjeta" in confirm.json()["reply_text"]
            # No envelope hint for transfers — paying the card is not new
            # consumption.
            assert confirm.json().get("open_screen") is None

        transfers = (
            await session.execute(
                select(Transfer).where(Transfer.user_id == user_id)
            )
        ).scalars().all()
        assert len(transfers) == 1
        transfer = transfers[0]
        assert transfer.from_account_id == corriente.id
        assert transfer.to_account_id == visa.id

        legs = (
            await session.execute(
                select(Transaction).where(
                    Transaction.transfer_id == transfer.id
                )
            )
        ).scalars().all()
        assert len(legs) == 2
        by_account = {leg.account_id: leg for leg in legs}
        assert Decimal(str(by_account[corriente.id].amount)) == Decimal("-80000")
        assert Decimal(str(by_account[visa.id].amount)) == Decimal("80000")
        assert all(leg.category == "transferencia" for leg in legs)

        # Undo removes the transfer AND both legs as one unit.
        user = await _get_user(session, user_id)
        ok, _msg = await run_undo(user=user, db=session, redis=get_redis())
        assert ok

        remaining_transfers = (
            await session.execute(
                select(Transfer).where(Transfer.user_id == user_id)
            )
        ).scalars().all()
        remaining_txns = (
            await session.execute(
                select(Transaction).where(Transaction.user_id == user_id)
            )
        ).scalars().all()
        assert remaining_transfers == []
        assert remaining_txns == []
    finally:
        _clear_db_override()
