"""Phase 6f — conversational recurring-income creation through the chat.

Second slice of chat-first creation (decision: "Conversational Creation Over
Forms", sequence goals → income → bill → debt). "me pagan 800 mil de salario
cada quincena el 15" → propose → confirm → RecurringIncome row. The CR-cycle
derive (aguinaldo + salario_escolar) stays the Incomes screen's one-tap action;
the confirmation points there.

Amount / frequency / next_payment_date are all NOT NULL, so the dispatcher
clarifies each when missing. Deterministic path covered via FixtureLLMClient +
direct dispatch.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.database import get_db
from api.main import app
from api.models.recurring_income import RecurringIncome
from api.models.user import User
from api.services.auth.magic_link import generate_link
from api.services.llm_extractor import ExtractionResult, Intent
from api.services.telegram_dispatcher import (
    AskClarification,
    ProposeAction,
    _resolve_next_payment_date,
    dispatch,
)
from bot.clarification import ClarificationState, merge_reply


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


def _income_extraction(**overrides) -> ExtractionResult:
    base = dict(
        intent=Intent.CREATE_INCOME,
        dispatcher="write",
        amount=Decimal("800000"),
        income_frequency="biweekly",
        income_next_date="el 15",
        confidence=0.9,
    )
    base.update(overrides)
    return ExtractionResult(**base)


# ── 1. Full flow: chat → proposal → confirm → RecurringIncome row ─────────────


@pytest.mark.asyncio
async def test_create_income_full_flow_commits_income(db_with_user):
    from tests.fixtures.extractor_responses import CREATE_INCOME_SALARY

    session, user_id = db_with_user
    token = await _setup_token(session, user_id, CREATE_INCOME_SALARY)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            propose = await _chat(
                ac, "me pagan 800 mil de salario cada quincena el 15", token
            )
            assert propose.status_code == 200, propose.text
            body = propose.json()
            assert " recurrente" in body["reply_text"].lower()
            # Salary in CRC → the proposal mentions the CR-cycle derive path.
            assert "aguinaldo" in body["reply_text"].lower()
            assert len(body["buttons"]) == 3

            confirm = await _chat(ac, "sí", token)
            assert confirm.status_code == 200, confirm.text
            assert "Ingreso recurrente creado" in confirm.json()["reply_text"]

        rows = (
            await session.execute(
                select(RecurringIncome).where(RecurringIncome.user_id == user_id)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].income_type == "salary"
        assert rows[0].amount == Decimal("800000")
        assert rows[0].frequency == "biweekly"
    finally:
        _clear_db_override()


# ── 2. Reject → nothing written ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_income_reject_writes_nothing(db_with_user):
    from tests.fixtures.extractor_responses import CREATE_INCOME_SALARY

    session, user_id = db_with_user
    token = await _setup_token(session, user_id, CREATE_INCOME_SALARY)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            assert (await _chat(ac, "configurá mi salario", token)).status_code == 200
            assert (await _chat(ac, "no", token)).status_code == 200

        rows = (
            await session.execute(
                select(RecurringIncome).where(RecurringIncome.user_id == user_id)
            )
        ).scalars().all()
        assert len(rows) == 0
    finally:
        _clear_db_override()


# ── 3-5. Each required field clarifies when missing ───────────────────────────


@pytest.mark.asyncio
async def test_create_income_missing_amount_clarifies(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    decision = await dispatch(
        extraction=_income_extraction(amount=None),
        user=user,
        today=date(2026, 5, 31),
        db=session,
    )
    assert isinstance(decision, AskClarification)
    assert decision.awaiting_field == "amount"


@pytest.mark.asyncio
async def test_create_income_missing_frequency_clarifies(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    decision = await dispatch(
        extraction=_income_extraction(income_frequency=None),
        user=user,
        today=date(2026, 5, 31),
        db=session,
    )
    assert isinstance(decision, AskClarification)
    assert decision.awaiting_field == "income_frequency"


@pytest.mark.asyncio
async def test_create_income_unresolvable_date_clarifies(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    decision = await dispatch(
        extraction=_income_extraction(income_next_date="cuando sea"),
        user=user,
        today=date(2026, 5, 31),
        db=session,
    )
    assert isinstance(decision, AskClarification)
    assert decision.awaiting_field == "income_next_date"


# ── 6. Proposal defaults income_type to salary + names it ─────────────────────


@pytest.mark.asyncio
async def test_create_income_defaults_type_and_name(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    decision = await dispatch(
        extraction=_income_extraction(income_type=None),
        user=user,
        today=date(2026, 5, 31),
        db=session,
    )
    assert isinstance(decision, ProposeAction)
    assert decision.action_type == "create_income"
    assert decision.payload["income_type"] == "salary"
    assert decision.payload["name"] == "Salario"
    assert decision.payload["next_payment_date"] == "2026-06-15"


# ── 7. Date resolver ──────────────────────────────────────────────────────────


def test_resolve_next_payment_date_cases():
    today = date(2026, 5, 31)
    assert _resolve_next_payment_date("el 15", today) == date(2026, 6, 15)
    assert _resolve_next_payment_date("día 5", today) == date(2026, 6, 5)
    assert _resolve_next_payment_date("fin de mes", today) == date(2026, 6, 30)
    assert _resolve_next_payment_date("hoy", today) == today
    assert _resolve_next_payment_date("2026-07-01", today) == date(2026, 7, 1)
    assert _resolve_next_payment_date("cuando pueda", today) is None
    assert _resolve_next_payment_date(None, today) is None


# ── 8. merge_reply folds frequency + next-date clarification replies ──────────


class _StubUser:
    currency = "CRC"


def test_merge_reply_income_frequency():
    state = ClarificationState(
        partial=_income_extraction(income_frequency=None).model_dump(mode="json"),
        awaiting_field="income_frequency",
        question_es="¿Cada cuánto te pagan?",
    )
    merged = merge_reply(state, "cada quincena", _StubUser())
    assert merged is not None
    assert merged.income_frequency == "biweekly"


def test_merge_reply_income_next_date_passthrough():
    state = ClarificationState(
        partial=_income_extraction(income_next_date=None).model_dump(mode="json"),
        awaiting_field="income_next_date",
        question_es="¿Cuándo es el próximo pago?",
    )
    merged = merge_reply(state, "el 15", _StubUser())
    assert merged is not None
    assert merged.income_next_date == "el 15"
