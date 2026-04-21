"""Fixture tests for the LLM extractor.

These tests DO NOT hit the Anthropic API. Each test pairs a Spanish input
with a pre-recorded RecordedLLMResponse and asserts the resulting
ExtractionResult matches expectations. This sharpens the schema before the
dispatcher consumes it.

Re-record the fixtures against the real API (and commit) when prompts or
the model version change. Fixture drift is a sign prompt-engineering needs
a look; don't "fix" it by loosening the assertions.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest

from api.services.llm_extractor import (
    ExtractionResult,
    FixtureLLMClient,
    Intent,
    extract_finance_intent,
)

from tests.fixtures.extractor_responses import (
    BASIC_EXPENSE_CRC,
    EXPENSE_YESTERDAY,
    LOW_CONFIDENCE_AMBIGUOUS,
    SLANG_AMOUNT_NO_CURRENCY,
    USD_EXPENSE,
    WEEKLY_BALANCE_QUERY,
)


# ── minimal fakes ─────────────────────────────────────────────────────────────
# A real User instance pulls SQLAlchemy mappers — we don't need any of that
# here. The runner only reads `.id` off the user, so a duck-typed stand-in
# is enough.


@dataclass
class _FakeUser:
    id: uuid.UUID = field(default_factory=uuid.uuid4)


class _StubSession:
    """Swallows db.add() / db.commit() so the runner can write its log row
    without a live Postgres. We don't assert on persistence here — the
    phase5b smoke script covers that end-to-end."""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_basic_crc_expense_shape():
    client = FixtureLLMClient(default=BASIC_EXPENSE_CRC)
    result = await extract_finance_intent(
        user=_FakeUser(),
        text="gasté 5000 colones en el super",
        client=client,
        model="claude-haiku-4-5",
        db=_StubSession(),
    )
    assert isinstance(result, ExtractionResult)
    assert result.intent is Intent.LOG_EXPENSE
    assert result.amount == Decimal("5000")
    assert result.currency == "CRC"
    assert result.category_hint == "supermercado"
    assert result.confidence >= 0.9


@pytest.mark.asyncio
async def test_slang_amount_no_currency_leaves_currency_null():
    """The spec says: if the user doesn't state a currency, leave it null
    and let the dispatcher default to user.currency. This test pins that
    contract — regressing to 'CRC' silently would hide currency bugs."""
    client = FixtureLLMClient(default=SLANG_AMOUNT_NO_CURRENCY)
    result = await extract_finance_intent(
        user=_FakeUser(),
        text="5k en gasolina",
        client=client,
        model="claude-haiku-4-5",
        db=_StubSession(),
    )
    assert result.intent is Intent.LOG_EXPENSE
    assert result.amount == Decimal("5000")
    assert result.currency is None
    assert result.category_hint == "combustible"


@pytest.mark.asyncio
async def test_usd_expense_normalizes_currency_uppercase():
    """The validator upper-cases currency. A lowercase 'usd' from a
    mis-prompted model should still land as 'USD'."""
    client = FixtureLLMClient(default=USD_EXPENSE)
    result = await extract_finance_intent(
        user=_FakeUser(),
        text="pagué 30 dólares en Amazon",
        client=client,
        model="claude-haiku-4-5",
        db=_StubSession(),
    )
    assert result.intent is Intent.LOG_EXPENSE
    assert result.amount == Decimal("30")
    assert result.currency == "USD"
    assert result.merchant == "Amazon"


@pytest.mark.asyncio
async def test_relative_date_passes_through_unresolved():
    """occurred_at_hint should be the user's literal phrase, not a resolved
    date. The dispatcher is what decodes 'ayer' into a calendar date in
    the user's timezone."""
    client = FixtureLLMClient(default=EXPENSE_YESTERDAY)
    result = await extract_finance_intent(
        user=_FakeUser(),
        text="ayer compré pan por 2 mil",
        client=client,
        model="claude-haiku-4-5",
        db=_StubSession(),
    )
    assert result.intent is Intent.LOG_EXPENSE
    assert result.occurred_at_hint == "ayer"
    assert result.amount == Decimal("2000")


@pytest.mark.asyncio
async def test_weekly_balance_query_window():
    client = FixtureLLMClient(default=WEEKLY_BALANCE_QUERY)
    result = await extract_finance_intent(
        user=_FakeUser(),
        text="¿cuánto gasté esta semana?",
        client=client,
        model="claude-haiku-4-5",
        db=_StubSession(),
    )
    assert result.intent is Intent.QUERY_BALANCE
    assert result.query_window == "this_week"
    assert result.amount is None


@pytest.mark.asyncio
async def test_low_confidence_ambiguous_preserved_for_dispatcher():
    """The schema must not silently promote low-confidence outputs to
    'log_expense' just because an amount was extracted. The dispatcher's
    confidence < 0.6 clarification branch depends on this field surviving
    validation verbatim."""
    client = FixtureLLMClient(default=LOW_CONFIDENCE_AMBIGUOUS)
    result = await extract_finance_intent(
        user=_FakeUser(),
        text="algo de 1000 por ahí",
        client=client,
        model="claude-haiku-4-5",
        db=_StubSession(),
    )
    assert result.intent is Intent.UNKNOWN
    assert result.confidence < 0.6
