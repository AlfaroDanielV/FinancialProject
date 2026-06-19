"""Phase 6f — conversational recurring-bill (gasto fijo) creation.

Third slice of chat-first creation. "el recibo de luz me llega como 18 mil
cada mes el 5" → propose → confirm → RecurringBill row + generated
BillOccurrences (mirrors POST /recurring-bills). amount_expected + frequency
are gathered conversationally; category falls back to a valid default
('servicios'); day_of_month is optional.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.database import get_db
from api.main import app
from api.models.bill_occurrence import BillOccurrence
from api.models.recurring_bill import RecurringBill
from api.models.user import User
from api.services.auth.magic_link import generate_link
from api.services.llm_extractor import ExtractionResult, Intent
from api.services.telegram_dispatcher import (
    AskClarification,
    ProposeAction,
    dispatch,
)
from bot.clarification import ClarificationState, merge_reply, _parse_bill_frequency_es


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


async def _setup_token(session, user_id, fixture_response):
    from bot.app import set_llm_client
    from api.services.llm_extractor import FixtureLLMClient

    set_llm_client(FixtureLLMClient(default=fixture_response))
    link = await generate_link(session, user_id=user_id, purpose="onboarding")
    _override_db(session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        exchange = await ac.post(
            "/api/v1/auth/magic-link/exchange", json={"token": link.raw_token}
        )
    assert exchange.status_code == 200, exchange.text
    return exchange.json()["token"]


def _bill_extraction(**overrides) -> ExtractionResult:
    base = dict(
        intent=Intent.CREATE_BILL,
        dispatcher="write",
        amount=Decimal("18000"),
        category_hint="servicios",
        bill_name="Luz",
        bill_frequency="monthly",
        bill_day_of_month=5,
        confidence=0.9,
    )
    base.update(overrides)
    return ExtractionResult(**base)


# ── 1. Full flow: chat → proposal → confirm → bill + occurrences ──────────────


@pytest.mark.asyncio
async def test_create_bill_full_flow_commits_bill_and_occurrences(db_with_user):
    from tests.fixtures.extractor_responses import CREATE_BILL_MONTHLY

    session, user_id = db_with_user
    token = await _setup_token(session, user_id, CREATE_BILL_MONTHLY)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            propose = await _chat(
                ac, "el recibo de luz me llega como 18 mil cada mes el 5", token
            )
            assert propose.status_code == 200, propose.text
            body = propose.json()
            assert "gasto fijo" in body["reply_text"].lower()
            assert len(body["buttons"]) == 3

            confirm = await _chat(ac, "sí", token)
            assert confirm.status_code == 200, confirm.text
            assert "Gasto fijo creado" in confirm.json()["reply_text"]

        bills = (
            await session.execute(
                select(RecurringBill).where(RecurringBill.user_id == user_id)
            )
        ).scalars().all()
        assert len(bills) == 1
        bill = bills[0]
        assert bill.name == "Luz"
        assert bill.category == "servicios"
        assert bill.amount_expected == Decimal("18000")
        assert bill.frequency == "monthly"
        assert bill.day_of_month == 5

        occ = (
            await session.execute(
                select(BillOccurrence).where(
                    BillOccurrence.recurring_bill_id == bill.id
                )
            )
        ).scalars().all()
        assert len(occ) > 0  # generate_occurrences ran
    finally:
        _clear_db_override()


# ── 2. Reject → nothing written ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_bill_reject_writes_nothing(db_with_user):
    from tests.fixtures.extractor_responses import CREATE_BILL_MONTHLY

    session, user_id = db_with_user
    token = await _setup_token(session, user_id, CREATE_BILL_MONTHLY)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            assert (await _chat(ac, "agregá el recibo de luz", token)).status_code == 200
            assert (await _chat(ac, "no", token)).status_code == 200

        bills = (
            await session.execute(
                select(RecurringBill).where(RecurringBill.user_id == user_id)
            )
        ).scalars().all()
        assert len(bills) == 0
    finally:
        _clear_db_override()


# ── 3-5. Required-field clarifications ────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_bill_missing_amount_clarifies(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    decision = await dispatch(
        extraction=_bill_extraction(amount=None),
        user=user, today=date(2026, 5, 31), db=session,
    )
    assert isinstance(decision, AskClarification)
    assert decision.awaiting_field == "amount"


@pytest.mark.asyncio
async def test_create_bill_missing_frequency_clarifies(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    decision = await dispatch(
        extraction=_bill_extraction(bill_frequency=None),
        user=user, today=date(2026, 5, 31), db=session,
    )
    assert isinstance(decision, AskClarification)
    assert decision.awaiting_field == "bill_frequency"


@pytest.mark.asyncio
async def test_create_bill_missing_name_clarifies(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    decision = await dispatch(
        extraction=_bill_extraction(bill_name=None),
        user=user, today=date(2026, 5, 31), db=session,
    )
    assert isinstance(decision, AskClarification)
    assert decision.awaiting_field == "bill_name"


# ── 6. Category falls back to a valid default; valid hint is kept ─────────────


@pytest.mark.asyncio
async def test_create_bill_category_defaulting(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    invalid = await dispatch(
        extraction=_bill_extraction(category_hint="no-es-categoria"),
        user=user, today=date(2026, 5, 31), db=session,
    )
    assert isinstance(invalid, ProposeAction)
    assert invalid.payload["category"] == "servicios"

    valid = await dispatch(
        extraction=_bill_extraction(category_hint="vivienda"),
        user=user, today=date(2026, 5, 31), db=session,
    )
    assert isinstance(valid, ProposeAction)
    assert valid.payload["category"] == "vivienda"
    assert valid.payload["start_date"] == "2026-05-31"


# ── 7. Frequency parser + merge_reply ─────────────────────────────────────────


def test_parse_bill_frequency_es():
    assert _parse_bill_frequency_es("bimestral") == "bimonthly"
    assert _parse_bill_frequency_es("cada tres meses") == "quarterly"
    assert _parse_bill_frequency_es("semestral") == "semiannual"
    assert _parse_bill_frequency_es("mensual") == "monthly"
    assert _parse_bill_frequency_es("anual") == "annual"
    assert _parse_bill_frequency_es("cuando sea") is None


class _StubUser:
    currency = "CRC"


def test_merge_reply_bill_frequency_and_name():
    state = ClarificationState(
        partial=_bill_extraction(bill_frequency=None).model_dump(mode="json"),
        awaiting_field="bill_frequency",
        question_es="¿Cada cuánto se paga?",
    )
    merged = merge_reply(state, "bimestral", _StubUser())
    assert merged is not None
    assert merged.bill_frequency == "bimonthly"

    state2 = ClarificationState(
        partial=_bill_extraction(bill_name=None).model_dump(mode="json"),
        awaiting_field="bill_name",
        question_es="¿Cómo se llama?",
    )
    merged2 = merge_reply(state2, "Internet", _StubUser())
    assert merged2 is not None
    assert merged2.bill_name == "Internet"
