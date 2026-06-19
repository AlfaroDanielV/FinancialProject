"""SINPE Móvil / transfer-receipt direction.

The LLM extracts the raw parties; a deterministic rule decides income / expense /
internal (never the LLM). Regression for the bug where an INCOMING SINPE receipt
("EDGAR … a nombre de DANIEL") was classified as an internal transfer and hit
"la cuenta origen y destino no pueden ser la misma."
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from api.models.account import Account
from api.models.user import User
from api.services.identity import is_user, name_matches, phone_matches
from api.services.dispatch.transfer_direction import classify_transfer_direction
from api.services.llm_extractor import ExtractionResult, Intent
from api.services.telegram_dispatcher import (
    AskClarification,
    ProposeAction,
    dispatch,
)
from bot.clarification import ClarificationState, merge_reply


def _receipt(**overrides) -> ExtractionResult:
    base = dict(
        intent=Intent.LOG_TRANSFER,
        dispatcher="write",
        amount=Decimal("16000"),
        currency="CRC",
        is_transfer_receipt=True,
        sender_name="EDGAR ALFREDO MENDOZA ORTIZ",
        sender_phone=None,
        recipient_name="DANIEL ALFARO VÍQUEZ",
        recipient_phone="85102997",
        confidence=0.95,
    )
    base.update(overrides)
    return ExtractionResult(**base)


def _user(full_name="Daniel Alfaro Víquez", phone="+506 8510-2997") -> User:
    return User(
        full_name=full_name, phone_number=phone, email="x@e.com",
        shortcut_token="x" * 48,
    )


# ── B1 identity ──────────────────────────────────────────────────────────────
def test_name_match_full_vs_partial_and_rejects_third_party():
    assert name_matches("DANIEL ALFARO VÍQUEZ", "Daniel Alfaro Víquez")
    assert name_matches("Daniel Alfaro", "Daniel Alfaro Víquez")  # receipt shows fewer
    assert not name_matches("EDGAR ALFREDO MENDOZA ORTIZ", "Daniel Alfaro Víquez")
    assert not name_matches("Daniel", "Daniel Alfaro Víquez")  # single token never matches


def test_phone_match_ignores_country_code_and_format():
    assert phone_matches("85102997", "+506 8510-2997")
    assert not phone_matches("70000000", "+506 8510-2997")


def test_is_user_phone_or_name():
    u = _user()
    assert is_user(u, name="DANIEL ALFARO VÍQUEZ")
    assert is_user(u, phone="8510-2997")
    assert not is_user(u, name="Edgar Mendoza", phone="70000000")


# ── B3 direction rule ────────────────────────────────────────────────────────
def test_rule_income_when_recipient_is_user():
    assert classify_transfer_direction(_receipt(), _user()) == "income"


def test_rule_expense_when_sender_is_user():
    e = _receipt(
        sender_name="DANIEL ALFARO VÍQUEZ", sender_phone="85102997",
        recipient_name="EDGAR ALFREDO MENDOZA ORTIZ", recipient_phone=None,
    )
    assert classify_transfer_direction(e, _user()) == "expense"


def test_rule_internal_when_both_are_user():
    e = _receipt(
        sender_name="Daniel Alfaro Víquez", recipient_name="Daniel Alfaro Víquez",
        sender_phone="85102997", recipient_phone="85102997",
    )
    assert classify_transfer_direction(e, _user()) == "internal"


def test_rule_unknown_when_neither_matches():
    e = _receipt(
        sender_name="Juan Pérez", recipient_name="María López", recipient_phone=None,
    )
    assert classify_transfer_direction(e, _user()) == "unknown"


# ── B4 dispatch ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_incoming_receipt_dispatches_as_income(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    user.full_name = "Daniel Alfaro Víquez"
    user.phone_number = "+506 8510-2997"
    session.add(Account(user_id=user_id, name="BAC", account_type="checking"))
    await session.commit()

    decision = await dispatch(
        extraction=_receipt(), user=user, today=date.today(), db=session
    )

    assert isinstance(decision, ProposeAction)
    assert decision.action_type == "log_income"  # NOT a transfer
    assert decision.payload["amount"] == "16000"  # positive (income)
    assert decision.payload["merchant"] == "EDGAR ALFREDO MENDOZA ORTIZ"


@pytest.mark.asyncio
async def test_ambiguous_receipt_asks_direction(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    user.full_name = "Daniel Alfaro Víquez"
    user.phone_number = "+506 8510-2997"
    await session.commit()

    e = _receipt(
        sender_name="Juan Pérez", recipient_name="María López", recipient_phone=None,
    )
    decision = await dispatch(
        extraction=e, user=user, today=date.today(), db=session
    )

    assert isinstance(decision, AskClarification)
    assert decision.awaiting_field == "transfer_direction"
    assert decision.options == ["Ingreso", "Gasto", "Entre mis cuentas"]


# ── B5 clarification merge ───────────────────────────────────────────────────
def test_merge_transfer_direction_income():
    e = _receipt(
        sender_name="Juan Pérez", recipient_name="María López", recipient_phone=None,
    )
    state = ClarificationState(
        partial=e.model_dump(mode="json"),
        awaiting_field="transfer_direction",
        question_es="¿ingreso, gasto o entre tus cuentas?",
    )
    merged = merge_reply(state, "Ingreso", _user())

    assert merged is not None
    assert merged.intent is Intent.LOG_INCOME
    assert merged.is_transfer_receipt is False  # consumed → no re-run of the rule
    assert merged.merchant == "Juan Pérez"  # payer surfaced


def test_merge_transfer_direction_internal_and_expense():
    state = ClarificationState(
        partial=_receipt().model_dump(mode="json"),
        awaiting_field="transfer_direction",
        question_es="?",
    )
    assert merge_reply(state, "Entre mis cuentas", _user()).intent is Intent.LOG_TRANSFER
    assert merge_reply(state, "Gasto", _user()).intent is Intent.LOG_EXPENSE
