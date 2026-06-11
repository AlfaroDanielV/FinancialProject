"""Phase 7b B4 — read-only chat tool get_card_analysis.

Same deterministic engine + live balance the native screen uses, so the chat
answer can't drift from the app. Covers: no cards → graceful error; single
card auto-resolves; multiple cards need a hint (ambiguous returns the names);
fuzzy hint resolution; registry keeps compare_periods last.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from api.models.account import Account
from api.models.credit_card_terms import CreditCardTerms
from api.models.transaction import Transaction

from app.queries.tools.base import list_tools_for_anthropic
from app.queries.tools import register_builtin_tools
from app.queries.tools.credit_cards import get_card_analysis


async def _add_card(
    session, user_id, name: str, *, owed: str | None = None
) -> Account:
    account = Account(
        user_id=user_id,
        name=name,
        account_type="credit",
        currency="CRC",
        initial_balance=Decimal("0"),
    )
    session.add(account)
    await session.flush()
    session.add(
        CreditCardTerms(
            user_id=user_id,
            account_id=account.id,
            annual_interest_rate=Decimal("0.45"),
            minimum_payment_pct=Decimal("0.025"),
            minimum_payment_floor=Decimal("5000"),
            payment_due_day=10,
        )
    )
    if owed is not None:
        session.add(
            Transaction(
                user_id=user_id,
                account_id=account.id,
                amount=-Decimal(owed),
                currency="CRC",
                transaction_date=date.today(),
                source="manual",
            )
        )
    await session.commit()
    await session.refresh(account)
    return account


@pytest.mark.asyncio
async def test_no_cards_returns_graceful_error(db_with_user):
    _session, user_id = db_with_user
    result = await get_card_analysis(user_id=user_id)
    assert result["error"] == "no_cards_with_terms"


@pytest.mark.asyncio
async def test_single_card_resolves_without_hint(db_with_user):
    session, user_id = db_with_user
    await _add_card(session, user_id, "BAC Visa", owed="400000")

    result = await get_card_analysis(user_id=user_id)
    assert result["card_name"] == "BAC Visa"
    assert Decimal(result["balance_owed"]) == Decimal("400000")
    assert Decimal(result["minimum_payment"]) == Decimal("10000.00")


@pytest.mark.asyncio
async def test_multiple_cards_need_a_hint(db_with_user):
    session, user_id = db_with_user
    await _add_card(session, user_id, "BAC Visa", owed="100000")
    await _add_card(session, user_id, "Promerica Master", owed="200000")

    ambiguous = await get_card_analysis(user_id=user_id)
    assert ambiguous["error"] == "ambiguous_card"
    assert sorted(ambiguous["available_card_names"]) == [
        "BAC Visa",
        "Promerica Master",
    ]

    resolved = await get_card_analysis(user_id=user_id, card_hint="promerica")
    assert resolved["card_name"] == "Promerica Master"
    assert Decimal(resolved["balance_owed"]) == Decimal("200000")


def test_registry_keeps_compare_periods_last():
    """Same contract test_tool_registry runs: from a clean registry, the
    explicit register_builtin_tools() order puts get_card_analysis in and
    keeps compare_periods as the cache-anchor (last)."""
    from app.queries.tools.base import _reset_registry_for_tests

    _reset_registry_for_tests()
    try:
        register_builtin_tools()
        names = [spec["name"] for spec in list_tools_for_anthropic()]
        assert "get_card_analysis" in names
        assert names[-1] == "compare_periods"
    finally:
        _reset_registry_for_tests()
        register_builtin_tools()
