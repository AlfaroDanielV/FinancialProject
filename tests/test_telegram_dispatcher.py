"""Unit tests for the deterministic dispatcher.

No LLM, no DB — the dispatcher is pure decision logic. We stub the two
helpers it calls into (`list_active`, `resolve_account`) via
monkeypatching on the imported module. The goal is to pin every branch
of `dispatch()` and every field of `ExtractionResult` it reads.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

import pytest

from api.services import telegram_dispatcher as td
from api.services.llm_extractor import ExtractionResult, Intent


# ── test fakes ────────────────────────────────────────────────────────────────


@dataclass
class _FakeAccount:
    name: str
    id: uuid.UUID = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.id is None:
            self.id = uuid.uuid4()


@dataclass
class _FakeUser:
    id: uuid.UUID
    currency: str = "CRC"


def _user() -> _FakeUser:
    return _FakeUser(id=uuid.uuid4())


def _stub_accounts(monkeypatch, accounts: list[_FakeAccount], resolved: Optional[_FakeAccount]):
    async def _list_active(user, db):
        return accounts

    async def _resolve_account(user, hint, db):
        return resolved

    monkeypatch.setattr(td, "list_active", _list_active)
    monkeypatch.setattr(td, "resolve_account", _resolve_account)


def _extraction(**overrides) -> ExtractionResult:
    base = {
        "intent": Intent.LOG_EXPENSE,
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


# ── structural intents short-circuit confidence ───────────────────────────────


async def test_confirm_yes_short_circuits(monkeypatch):
    _stub_accounts(monkeypatch, [], None)
    result = await td.dispatch(
        extraction=_extraction(intent=Intent.CONFIRM_YES, confidence=0.1),
        user=_user(),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(result, td.ConfirmResponse)
    assert result.yes is True


async def test_confirm_no_short_circuits(monkeypatch):
    _stub_accounts(monkeypatch, [], None)
    result = await td.dispatch(
        extraction=_extraction(intent=Intent.CONFIRM_NO, confidence=0.1),
        user=_user(),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(result, td.ConfirmResponse)
    assert result.yes is False


async def test_undo_short_circuits(monkeypatch):
    _stub_accounts(monkeypatch, [], None)
    result = await td.dispatch(
        extraction=_extraction(intent=Intent.UNDO, confidence=0.4),
        user=_user(),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(result, td.UndoRequest)


async def test_unknown_intent_routes_to_help(monkeypatch):
    _stub_accounts(monkeypatch, [], None)
    result = await td.dispatch(
        extraction=_extraction(intent=Intent.UNKNOWN, confidence=0.2),
        user=_user(),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(result, td.ShowHelp)


async def test_help_intent_routes_to_help(monkeypatch):
    _stub_accounts(monkeypatch, [], None)
    result = await td.dispatch(
        extraction=_extraction(intent=Intent.HELP, confidence=0.9),
        user=_user(),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(result, td.ShowHelp)


# ── confidence floor ─────────────────────────────────────────────────────────


async def test_low_confidence_log_expense_clarifies(monkeypatch):
    _stub_accounts(monkeypatch, [], None)
    result = await td.dispatch(
        extraction=_extraction(intent=Intent.LOG_EXPENSE, confidence=0.4),
        user=_user(),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(result, td.AskClarification)
    assert result.awaiting_field == "intent"


# ── log_expense: amount missing ───────────────────────────────────────────────


async def test_log_expense_missing_amount_clarifies(monkeypatch):
    _stub_accounts(monkeypatch, [], None)
    result = await td.dispatch(
        extraction=_extraction(amount=None, confidence=0.9),
        user=_user(),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(result, td.AskClarification)
    assert result.awaiting_field == "amount"


# ── log_expense: account resolution ───────────────────────────────────────────


async def test_log_expense_single_account_auto_selects(monkeypatch):
    only = _FakeAccount(name="BAC")
    _stub_accounts(monkeypatch, [only], only)
    result = await td.dispatch(
        extraction=_extraction(),
        user=_user(),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(result, td.ProposeAction)
    assert result.payload["account_id"] == str(only.id)
    assert "BAC" in result.summary_es


async def test_log_expense_multiple_accounts_no_hint_clarifies(monkeypatch):
    a = _FakeAccount(name="BAC")
    b = _FakeAccount(name="BCR")
    _stub_accounts(monkeypatch, [a, b], None)  # resolve_account returns None
    result = await td.dispatch(
        extraction=_extraction(account_hint=None),
        user=_user(),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(result, td.AskClarification)
    assert result.awaiting_field == "account"


async def test_log_expense_multiple_accounts_ambiguous_hint_clarifies(monkeypatch):
    a = _FakeAccount(name="BAC")
    b = _FakeAccount(name="BCR")
    _stub_accounts(monkeypatch, [a, b], None)
    result = await td.dispatch(
        extraction=_extraction(account_hint="banco"),
        user=_user(),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(result, td.AskClarification)
    assert result.awaiting_field == "account"


async def test_log_expense_multiple_accounts_hint_resolves(monkeypatch):
    a = _FakeAccount(name="BAC")
    b = _FakeAccount(name="BCR")
    _stub_accounts(monkeypatch, [a, b], a)  # resolve_account found "BAC"
    result = await td.dispatch(
        extraction=_extraction(account_hint="bac"),
        user=_user(),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(result, td.ProposeAction)
    assert result.payload["account_id"] == str(a.id)


# ── log_expense: currency defaulting ──────────────────────────────────────────


async def test_log_expense_null_currency_defaults_to_user_currency(monkeypatch):
    only = _FakeAccount(name="BAC")
    _stub_accounts(monkeypatch, [only], only)
    result = await td.dispatch(
        extraction=_extraction(currency=None),
        user=_FakeUser(id=uuid.uuid4(), currency="CRC"),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(result, td.ProposeAction)
    assert result.payload["currency"] == "CRC"
    assert "por defecto" in result.summary_es.lower()


# ── log_income: sign flipped ──────────────────────────────────────────────────


async def test_log_income_yields_positive_amount(monkeypatch):
    only = _FakeAccount(name="BAC")
    _stub_accounts(monkeypatch, [only], only)
    result = await td.dispatch(
        extraction=_extraction(intent=Intent.LOG_INCOME, amount=Decimal("100000")),
        user=_user(),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(result, td.ProposeAction)
    assert result.action_type == "log_income"
    assert Decimal(result.payload["amount"]) == Decimal("100000")


async def test_log_expense_yields_negative_amount(monkeypatch):
    only = _FakeAccount(name="BAC")
    _stub_accounts(monkeypatch, [only], only)
    result = await td.dispatch(
        extraction=_extraction(intent=Intent.LOG_EXPENSE, amount=Decimal("7500")),
        user=_user(),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(result, td.ProposeAction)
    assert Decimal(result.payload["amount"]) == Decimal("-7500")


# ── occurred_at resolution ────────────────────────────────────────────────────


async def test_log_expense_yesterday_resolves(monkeypatch):
    only = _FakeAccount(name="BAC")
    _stub_accounts(monkeypatch, [only], only)
    result = await td.dispatch(
        extraction=_extraction(occurred_at_hint="ayer"),
        user=_user(),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(result, td.ProposeAction)
    assert result.payload["transaction_date"] == "2026-04-19"


async def test_log_expense_unknown_hint_falls_back_to_today(monkeypatch):
    only = _FakeAccount(name="BAC")
    _stub_accounts(monkeypatch, [only], only)
    result = await td.dispatch(
        extraction=_extraction(occurred_at_hint="el día de san juan"),
        user=_user(),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(result, td.ProposeAction)
    assert result.payload["transaction_date"] == "2026-04-20"


# ── queries: window resolution ────────────────────────────────────────────────


async def test_query_balance_resolves_window(monkeypatch):
    _stub_accounts(monkeypatch, [], None)
    result = await td.dispatch(
        extraction=_extraction(
            intent=Intent.QUERY_BALANCE,
            amount=None,
            currency=None,
            merchant=None,
            category_hint=None,
            query_window="this_month",
            confidence=0.9,
        ),
        user=_user(),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(result, td.RunQuery)
    assert result.query_kind == "balance"
    assert result.window_start == date(2026, 4, 1)
    assert result.window_end == date(2026, 4, 20)


async def test_query_recent_uses_default_window(monkeypatch):
    _stub_accounts(monkeypatch, [], None)
    # Extractor returns query_window=None → dispatcher should pick a default.
    result = await td.dispatch(
        extraction=_extraction(
            intent=Intent.QUERY_RECENT,
            amount=None,
            currency=None,
            merchant=None,
            category_hint=None,
            query_window=None,
            confidence=0.9,
        ),
        user=_user(),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(result, td.RunQuery)
    assert result.query_kind == "recent"
    # this_week starts on Monday 2026-04-20 (which is a Monday itself)
    assert result.window_start == date(2026, 4, 20)
    assert result.limit == td.DEFAULT_RECENT_LIMIT


# ── clarification round-trip ──────────────────────────────────────────────────
# Pins the bug from the first live test: dispatcher asks "¿De qué cuenta?",
# the user replies "Promerica Visa Platinum", and the reply must end up as a
# ProposeAction on the pre-existing partial — NOT re-extracted as intent=unknown.


async def test_account_clarification_round_trip_merges_and_proposes(monkeypatch):
    from bot.clarification import ClarificationState, merge_reply

    a = _FakeAccount(name="BAC")
    b = _FakeAccount(name="Promerica Visa Platinum")

    # First dispatch: multiple active accounts, no hint → AskClarification.
    _stub_accounts(monkeypatch, [a, b], None)
    first = await td.dispatch(
        extraction=_extraction(account_hint=None),
        user=_user(),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(first, td.AskClarification)
    assert first.awaiting_field == "account"
    assert first.partial["intent"] == Intent.LOG_EXPENSE.value
    assert first.partial["amount"] == "5000"

    # User replies with the account name. merge_reply should fold it into
    # the partial and produce a valid ExtractionResult.
    state = ClarificationState(
        partial=first.partial,
        awaiting_field=first.awaiting_field,
        question_es=first.question_es,
    )
    merged = merge_reply(state, "Promerica Visa Platinum", _user())
    assert merged is not None
    assert merged.account_hint == "Promerica Visa Platinum"
    assert merged.intent is Intent.LOG_EXPENSE
    assert merged.amount == Decimal("5000")

    # Second dispatch: resolve_account now hits `b`. Should propose.
    _stub_accounts(monkeypatch, [a, b], b)
    second = await td.dispatch(
        extraction=merged,
        user=_user(),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(second, td.ProposeAction)
    assert second.payload["account_id"] == str(b.id)
    assert "Promerica Visa Platinum" in second.summary_es


async def test_amount_clarification_round_trip_parses_cr_format(monkeypatch):
    """User asked for an amount, types '72.679,00' (CR convention).
    Must parse to 72679 and come back as ProposeAction."""
    from bot.clarification import ClarificationState, merge_reply

    only = _FakeAccount(name="BAC")

    _stub_accounts(monkeypatch, [only], only)
    first = await td.dispatch(
        extraction=_extraction(amount=None),
        user=_user(),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(first, td.AskClarification)
    assert first.awaiting_field == "amount"

    state = ClarificationState(
        partial=first.partial,
        awaiting_field=first.awaiting_field,
        question_es=first.question_es,
    )
    merged = merge_reply(state, "72.679,00", _user())
    assert merged is not None
    assert merged.amount == Decimal("72679.00")

    second = await td.dispatch(
        extraction=merged,
        user=_user(),
        today=date(2026, 4, 20),
        db=object(),
    )
    assert isinstance(second, td.ProposeAction)
    assert Decimal(second.payload["amount"]) == Decimal("-72679.00")


async def test_clarification_merge_rejects_gibberish_amount(monkeypatch):
    """If the user's reply can't be interpreted, merge_reply returns None
    so the pipeline can re-ask the same question instead of silently
    committing garbage."""
    from bot.clarification import ClarificationState, merge_reply

    state = ClarificationState(
        partial=_extraction(amount=None).model_dump(mode="json"),
        awaiting_field="amount",
        question_es="¿Cuánto fue?",
    )
    assert merge_reply(state, "no sé, algo así", _user()) is None
