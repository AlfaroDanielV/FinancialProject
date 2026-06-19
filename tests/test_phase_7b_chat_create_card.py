"""Phase 7b B3 — conversational credit-card creation: chat → native form handoff.

Like debt (Phase 6f D1), the chat never commits a card: `Intent.CREATE_CARD`
does light extraction and returns OpenScreenAction("card_create", prefill);
the native form (+ optional statement PDF) gathers the terms and submits to
POST /accounts + PUT /accounts/{id}/card-terms. Telegram ignores open_screen.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from api.models.user import User
from api.services.llm_extractor import ExtractionResult, Intent
from api.services.telegram_dispatcher import OpenScreenAction, dispatch


async def _get_user(session, user_id) -> User:
    return (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one()


def _card_extraction(**overrides) -> ExtractionResult:
    base = dict(
        intent=Intent.CREATE_CARD,
        dispatcher="write",
        confidence=0.9,
    )
    base.update(overrides)
    return ExtractionResult(**base)


@pytest.mark.asyncio
async def test_create_card_opens_native_form_with_prefill(db_with_user):
    session, user_id = db_with_user
    user = await _get_user(session, user_id)

    decision = await dispatch(
        extraction=_card_extraction(
            card_issuer="BAC", card_limit=Decimal("2000000")
        ),
        user=user,
        today=date.today(),
        db=session,
    )
    assert isinstance(decision, OpenScreenAction), decision
    assert decision.screen == "card_create"
    assert decision.prefill["issuer"] == "BAC"
    assert decision.prefill["credit_limit"] == "2000000"
    assert decision.prefill["currency"] == "CRC"  # user default
    assert "formulario de tarjeta" in decision.message_es


@pytest.mark.asyncio
async def test_create_card_with_no_details_still_hands_off(db_with_user):
    """'quiero agregar mi tarjeta de crédito' with every field null must open
    the form, not clarify — the form gathers everything (debt precedent)."""
    session, user_id = db_with_user
    user = await _get_user(session, user_id)

    decision = await dispatch(
        extraction=_card_extraction(confidence=0.55),  # below CONFIDENCE_FLOOR
        user=user,
        today=date.today(),
        db=session,
    )
    assert isinstance(decision, OpenScreenAction), decision
    assert decision.screen == "card_create"
    assert decision.prefill["name"] is None
    assert decision.prefill["issuer"] is None
    assert decision.prefill["credit_limit"] is None


@pytest.mark.asyncio
async def test_create_debt_still_routes_to_debt_form(db_with_user):
    """Regression: the card intent must not swallow debt creation."""
    session, user_id = db_with_user
    user = await _get_user(session, user_id)

    decision = await dispatch(
        extraction=ExtractionResult(
            intent=Intent.CREATE_DEBT,
            dispatcher="write",
            debt_principal=Decimal("5000000"),
            confidence=0.9,
        ),
        user=user,
        today=date.today(),
        db=session,
    )
    assert isinstance(decision, OpenScreenAction), decision
    assert decision.screen == "debt_create"
