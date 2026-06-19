"""Phase 6f — conversational goal creation through the chat pipeline.

The native app's chat is the creation surface (decision: "Conversational
Creation Over Forms"). A user says "creá una meta de fondo de emergencia de
500 mil"; the extractor proposes a create_goal; the deterministic dispatcher
asks for any missing field, then proposes; on "Sí" a Goal row is written —
the same row the SPA/REST create_goal produces.

The deterministic path (dispatch → propose → confirm → write) is covered
without the real LLM via the FixtureLLMClient and direct dispatch() calls.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.database import get_db
from api.main import app
from api.models.goal import Goal
from api.models.user import User
from api.services.auth.magic_link import generate_link
from api.services.llm_extractor import ExtractionResult, Intent
from api.services.telegram_dispatcher import (
    AskClarification,
    ProposeAction,
    _resolve_goal_target_date,
    dispatch,
)
from bot import messages_es
from bot.clarification import ClarificationState, merge_reply


# ── helpers ───────────────────────────────────────────────────────────────────


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
    """Set the fixture LLM client and return a bearer token. No account needed
    — goals don't reference one."""
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


def _goal_extraction(**overrides) -> ExtractionResult:
    base = dict(
        intent=Intent.CREATE_GOAL,
        dispatcher="write",
        goal_name="fondo de emergencia",
        goal_target_amount=Decimal("500000"),
        confidence=0.9,
    )
    base.update(overrides)
    return ExtractionResult(**base)


# ── 1. Full flow: chat message → proposal → confirm → Goal row ────────────────


@pytest.mark.asyncio
async def test_create_goal_full_flow_commits_goal(db_with_user):
    from tests.fixtures.extractor_responses import CREATE_GOAL_NAMED

    session, user_id = db_with_user
    token = await _setup_token(session, user_id, CREATE_GOAL_NAMED)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            propose = await _chat(
                ac, "creá una meta de fondo de emergencia de 500 mil", token
            )
            assert propose.status_code == 200, propose.text
            body = propose.json()
            assert "meta" in body["reply_text"].lower()
            assert len(body["buttons"]) == 3  # Sí / No / Editar

            confirm = await _chat(ac, "sí", token)
            assert confirm.status_code == 200, confirm.text
            assert "Meta creada" in confirm.json()["reply_text"]

        goals = (
            await session.execute(select(Goal).where(Goal.user_id == user_id))
        ).scalars().all()
        assert len(goals) == 1
        assert goals[0].name == "fondo de emergencia"
        assert goals[0].target_amount == Decimal("500000")
    finally:
        _clear_db_override()


# ── 2. Reject → no Goal row ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_goal_reject_writes_nothing(db_with_user):
    from tests.fixtures.extractor_responses import CREATE_GOAL_NAMED

    session, user_id = db_with_user
    token = await _setup_token(session, user_id, CREATE_GOAL_NAMED)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            propose = await _chat(ac, "meta de fondo de emergencia 500 mil", token)
            assert propose.status_code == 200
            cancel = await _chat(ac, "no", token)
            assert cancel.status_code == 200

        goals = (
            await session.execute(select(Goal).where(Goal.user_id == user_id))
        ).scalars().all()
        assert len(goals) == 0
    finally:
        _clear_db_override()


# ── 3. Missing target amount → clarification ──────────────────────────────────


@pytest.mark.asyncio
async def test_create_goal_missing_amount_asks_clarification(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    extraction = _goal_extraction(goal_target_amount=None, goal_name=None)
    decision = await dispatch(
        extraction=extraction, user=user, today=date(2026, 5, 31), db=session
    )
    assert isinstance(decision, AskClarification)
    assert decision.awaiting_field == "goal_target_amount"


# ── 4. Has amount, missing name → clarification ───────────────────────────────


@pytest.mark.asyncio
async def test_create_goal_missing_name_asks_clarification(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    extraction = _goal_extraction(goal_name=None)
    decision = await dispatch(
        extraction=extraction, user=user, today=date(2026, 5, 31), db=session
    )
    assert isinstance(decision, AskClarification)
    assert decision.awaiting_field == "goal_name"


# ── 5. Forecast (monthly needed) appears in the proposal summary ──────────────


@pytest.mark.asyncio
async def test_create_goal_summary_has_monthly_forecast(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    # 500_000 over 10 months → ~50_000/mes.
    extraction = _goal_extraction(goal_target_date="en 10 meses")
    decision = await dispatch(
        extraction=extraction, user=user, today=date(2026, 5, 31), db=session
    )
    assert isinstance(decision, ProposeAction)
    assert decision.action_type == "create_goal"
    assert "/mes" in decision.summary_es
    assert decision.payload["target_date"] is not None


# ── 6. Date resolver ──────────────────────────────────────────────────────────


def test_resolve_goal_target_date_cases():
    today = date(2026, 5, 31)
    assert _resolve_goal_target_date("diciembre", today) == date(2026, 12, 1)
    # A month already past this year rolls to next year.
    assert _resolve_goal_target_date("marzo", today) == date(2027, 3, 1)
    assert _resolve_goal_target_date("2027-03", today) == date(2027, 3, 1)
    assert _resolve_goal_target_date("2026-08-15", today) == date(2026, 8, 15)
    assert _resolve_goal_target_date("en 6 meses", today) == date(2026, 11, 30)
    assert _resolve_goal_target_date("fin de año", today) == date(2026, 12, 1)
    assert _resolve_goal_target_date("cuando sea", today) is None
    assert _resolve_goal_target_date(None, today) is None


# ── 7. merge_reply folds goal clarification replies into the partial ──────────


class _StubUser:
    """merge_reply only touches user attributes synchronously; no DB needed."""

    currency = "CRC"


def test_merge_reply_goal_target_amount():
    state = ClarificationState(
        partial=_goal_extraction(goal_target_amount=None, goal_name=None).model_dump(
            mode="json"
        ),
        awaiting_field="goal_target_amount",
        question_es="¿Cuánto querés ahorrar?",
    )
    merged = merge_reply(state, "2 millones", _StubUser())
    assert merged is not None
    assert merged.intent is Intent.CREATE_GOAL
    assert merged.goal_target_amount == Decimal("2000000")


def test_merge_reply_goal_name():
    state = ClarificationState(
        partial=_goal_extraction(goal_name=None).model_dump(mode="json"),
        awaiting_field="goal_name",
        question_es="¿Cómo querés llamar la meta?",
    )
    merged = merge_reply(state, "vacaciones", _StubUser())
    assert merged is not None
    assert merged.goal_name == "vacaciones"
