"""Phase 6f — conversational debt creation (D1): chat → native form handoff.

Fourth (and last) slice of chat-first creation, but the ODD ONE OUT: debt does
NOT confirm/commit in chat. DebtCreate needs six required fields + amortization
params, so the chat does LIGHT extraction and returns an OpenScreenAction that
tells the native client to open the pre-filled DebtCreateScreen. The
deterministic POST /debts (built in D3/D4) stays the only write path — nothing
is committed here.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.database import get_db
from api.main import app
from api.models.debt import Debt
from api.models.user import User
from api.services.auth.magic_link import generate_link
from api.services.llm_extractor import ExtractionResult, Intent
from api.services.telegram_dispatcher import (
    AskClarification,
    OpenScreenAction,
    dispatch,
)


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


def _debt_extraction(**overrides) -> ExtractionResult:
    base = dict(
        intent=Intent.CREATE_DEBT,
        dispatcher="write",
        debt_principal=Decimal("5000000"),
        debt_term_months=60,
        debt_lender="BAC",
        confidence=0.9,
    )
    base.update(overrides)
    return ExtractionResult(**base)


# ── 1. Dispatch → OpenScreenAction with the expected prefill ──────────────────


@pytest.mark.asyncio
async def test_create_debt_dispatch_returns_open_screen(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    decision = await dispatch(
        extraction=_debt_extraction(),
        user=user, today=date(2026, 6, 1), db=session,
    )
    assert isinstance(decision, OpenScreenAction)
    assert decision.screen == "debt_create"
    p = decision.prefill
    # Amounts pass through as strings (no float drift); current_balance defaults
    # to the principal for a brand-new loan.
    assert p["original_amount"] == "5000000"
    assert p["current_balance"] == "5000000"
    assert p["term_months"] == 60
    assert p["lender"] == "BAC"
    assert p["name"] is None
    # Rate unknown → null; the form asks or reads it from the uploaded PDF.
    assert p["interest_rate_pct"] is None
    # Currency defaults to the user's preferred currency when not stated.
    assert p["currency"] == user.currency


# ── 2. Rate stated → percent passes through unchanged (form converts) ─────────


@pytest.mark.asyncio
async def test_create_debt_rate_passthrough_as_percent(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    decision = await dispatch(
        extraction=_debt_extraction(
            debt_interest_rate=Decimal("12"), debt_name="crédito de carro"
        ),
        user=user, today=date(2026, 6, 1), db=session,
    )
    assert isinstance(decision, OpenScreenAction)
    assert decision.prefill["interest_rate_pct"] == "12"
    assert decision.prefill["name"] == "crédito de carro"


# ── 3. Chat endpoint emits open_screen, no buttons, writes nothing ────────────


@pytest.mark.asyncio
async def test_create_debt_chat_endpoint_open_screen_no_commit(db_with_user):
    from tests.fixtures.extractor_responses import CREATE_DEBT_BASIC

    session, user_id = db_with_user
    token = await _setup_token(session, user_id, CREATE_DEBT_BASIC)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await _chat(
                ac, "tengo un préstamo de 5 millones a 5 años con el BAC", token
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Handoff: open_screen present, no confirm/cancel buttons (no commit step).
        assert body["open_screen"] is not None
        assert body["open_screen"]["screen"] == "debt_create"
        assert body["open_screen"]["prefill"]["original_amount"] == "5000000"
        assert body["open_screen"]["prefill"]["lender"] == "BAC"
        assert body["buttons"] == []
        assert body["reply_text"]  # non-empty Spanish guidance

        # Nothing was written — the form posts to POST /debts later.
        debts = (
            await session.execute(select(Debt).where(Debt.user_id == user_id))
        ).scalars().all()
        assert len(debts) == 0
    finally:
        _clear_db_override()


# ── 4. Out-of-range light fields drop to null (validator guard) ───────────────


@pytest.mark.asyncio
async def test_create_debt_invalid_rate_and_term_dropped(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    decision = await dispatch(
        extraction=_debt_extraction(
            debt_interest_rate=Decimal("250"),  # > 100% → dropped
            debt_term_months=9999,               # > 600 → dropped
            debt_principal=None,                 # form gathers it
        ),
        user=user, today=date(2026, 6, 1), db=session,
    )
    assert isinstance(decision, OpenScreenAction)
    assert decision.prefill["interest_rate_pct"] is None
    assert decision.prefill["term_months"] is None
    assert decision.prefill["original_amount"] is None
    assert decision.prefill["current_balance"] is None


# ── 5. Low-confidence bare debt request still opens the form (bug fix) ─────────
# "Saqué un préstamo" / "Quiero registrar una deuda" carry no amount, so the
# model emits create_debt with low confidence. The confidence floor must NOT
# divert it to the generic "¿gasto, ingreso o consulta?" clarification — that
# question is wrong for an explicit debt request and trapped the user in a loop.


@pytest.mark.asyncio
async def test_create_debt_below_confidence_floor_still_opens_form(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    decision = await dispatch(
        extraction=_debt_extraction(
            confidence=0.45,            # below CONFIDENCE_FLOOR (0.6)
            debt_principal=None,        # bare request — no details given
            debt_term_months=None,
            debt_lender=None,
        ),
        user=user, today=date(2026, 6, 1), db=session,
    )
    assert isinstance(decision, OpenScreenAction)
    assert decision.screen == "debt_create"


@pytest.mark.asyncio
async def test_low_confidence_log_expense_still_clarifies(db_with_user):
    """Regression guard: the confidence floor STILL fires for log intents —
    "¿gasto, ingreso o consulta?" is the right question there."""
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    extraction = ExtractionResult(
        intent=Intent.LOG_EXPENSE,
        dispatcher="write",
        amount=Decimal("5000"),
        confidence=0.4,
    )
    decision = await dispatch(
        extraction=extraction, user=user, today=date(2026, 6, 1), db=session
    )
    assert isinstance(decision, AskClarification)
    assert decision.awaiting_field == "intent"
