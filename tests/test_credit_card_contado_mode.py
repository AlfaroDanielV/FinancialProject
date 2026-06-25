"""Credit-card recurring payment — contado (full-balance) mode.

A card's `payment_mode` selects which LIVE figure the upcoming-payment
projection surfaces: the minimum (default, the must-pay floor) or the full
balance paid "de contado". It is a projection only — never a materialized
recurring_bills row. Per the operator decision, contado drives the
reminder + the agent's awareness ONLY; the budget (envelope reservation +
affordability transparency) stays on the minimum.

Covers:
- `CardWithTerms.recurring_payment_due` (full → balance, minimum → minimum).
- The feed projects the contado amount + a "(de contado)" title + payment_mode;
  minimum-mode projection is unchanged (regression).
- Envelope reservation stays on the minimum even for a full-mode card.
- `gather_affordability_inputs` is identical regardless of payment_mode.
- The read-only `list_recurring_bills` chat tool surfaces the card payment
  with the contado amount (so the agent "takes it into account").
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from api.models.account import Account
from api.models.credit_card_terms import CreditCardTerms
from api.models.envelope import Envelope
from api.models.transaction import Transaction
from api.models.user import User
from api.services.credit_cards import list_active_cards_with_terms
from api.services.envelopes import compute_envelope_summary
from api.services.finance.affordability import gather_affordability_inputs
from api.services.recurrence import get_upcoming_feed

from app.queries.tools.recurring_bills import list_recurring_bills


async def _get_user(session, user_id) -> User:
    return (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one()


async def _add_card(session, user_id, name="BAC Visa"):
    account = Account(
        user_id=user_id,
        name=name,
        account_type="credit",
        currency="CRC",
        initial_balance=Decimal("0"),
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _add_terms(session, user_id, account_id, *, payment_mode="minimum", envelope_id=None):
    terms = CreditCardTerms(
        user_id=user_id,
        account_id=account_id,
        annual_interest_rate=Decimal("0.45"),
        minimum_payment_pct=Decimal("0.025"),
        minimum_payment_floor=Decimal("5000"),
        payment_due_day=10,
        payment_mode=payment_mode,
        envelope_id=envelope_id,
    )
    session.add(terms)
    await session.commit()
    await session.refresh(terms)
    return terms


async def _charge(session, user_id, account_id, amount: str):
    session.add(
        Transaction(
            user_id=user_id,
            account_id=account_id,
            amount=Decimal(amount),
            currency="CRC",
            transaction_date=date.today(),
            source="manual",
        )
    )
    await session.commit()


async def _add_envelope(session, user_id, name="Deudas", limit="500000"):
    env = Envelope(
        user_id=user_id,
        name=name,
        envelope_class="needs",
        limit_amount=Decimal(limit),
        currency="CRC",
    )
    session.add(env)
    await session.commit()
    await session.refresh(env)
    return env


@pytest.mark.asyncio
async def test_recurring_payment_due_property(db_with_user):
    session, user_id = db_with_user
    card = await _add_card(session, user_id)
    await _add_terms(session, user_id, card.id, payment_mode="minimum")
    await _charge(session, user_id, card.id, "-400000")

    cards = await list_active_cards_with_terms(session, user_id=user_id)
    assert len(cards) == 1
    c = cards[0]
    assert c.balance_owed == Decimal("400000")
    assert c.minimum_due == Decimal("10000.00")  # 2.5% × 400k
    # minimum mode → recurring payment is the minimum
    assert c.recurring_payment_due == c.minimum_due

    # flip to contado → recurring payment is the full balance
    c.terms.payment_mode = "full"
    await session.commit()
    cards = await list_active_cards_with_terms(session, user_id=user_id)
    assert cards[0].recurring_payment_due == Decimal("400000")


@pytest.mark.asyncio
async def test_feed_projects_contado_full_balance(db_with_user):
    session, user_id = db_with_user
    card = await _add_card(session, user_id, "BAC Visa")
    await _add_terms(session, user_id, card.id, payment_mode="full")
    await _charge(session, user_id, card.id, "-400000")

    today = date.today()
    entries = await get_upcoming_feed(
        session, user_id, from_date=today, to_date=today + timedelta(days=60)
    )
    card_entries = [e for e in entries if e.item_type == "card_payment"]
    assert len(card_entries) == 1
    entry = card_entries[0]
    assert Decimal(str(entry.amount)) == Decimal("400000")  # full balance, not the minimum
    assert entry.payment_mode == "full"
    assert "de contado" in entry.title


@pytest.mark.asyncio
async def test_feed_minimum_mode_unchanged(db_with_user):
    session, user_id = db_with_user
    card = await _add_card(session, user_id, "BAC Visa")
    await _add_terms(session, user_id, card.id, payment_mode="minimum")
    await _charge(session, user_id, card.id, "-400000")

    today = date.today()
    entries = await get_upcoming_feed(
        session, user_id, from_date=today, to_date=today + timedelta(days=60)
    )
    entry = next(e for e in entries if e.item_type == "card_payment")
    assert Decimal(str(entry.amount)) == Decimal("10000.00")  # minimum
    assert entry.payment_mode == "minimum"
    assert "de contado" not in entry.title


@pytest.mark.asyncio
async def test_reservation_uses_minimum_even_in_full_mode(db_with_user):
    session, user_id = db_with_user
    card = await _add_card(session, user_id, "BAC Visa")
    env = await _add_envelope(session, user_id)
    await _add_terms(
        session, user_id, card.id, payment_mode="full", envelope_id=env.id
    )
    await _charge(session, user_id, card.id, "-400000")

    user = await _get_user(session, user_id)
    summary = await compute_envelope_summary(session, user=user)
    item = next(i for i in summary.envelopes if str(i.id) == str(env.id))
    # Budget stays on the minimum (must-pay floor), NOT the full balance.
    assert Decimal(str(item.reserved)) == Decimal("10000.00")


@pytest.mark.asyncio
async def test_affordability_inputs_identical_regardless_of_mode(db_with_user):
    session, user_id = db_with_user
    card = await _add_card(session, user_id, "BAC Visa")
    terms = await _add_terms(session, user_id, card.id, payment_mode="minimum")
    await _charge(session, user_id, card.id, "-400000")
    user = await _get_user(session, user_id)

    minimum_inputs = await gather_affordability_inputs(session, user=user)

    terms.payment_mode = "full"
    await session.commit()
    full_inputs = await gather_affordability_inputs(session, user=user)

    assert (
        full_inputs.monthly_debt_payments
        == minimum_inputs.monthly_debt_payments
    )


@pytest.mark.asyncio
async def test_list_recurring_bills_tool_surfaces_card_contado(db_with_user):
    session, user_id = db_with_user
    card = await _add_card(session, user_id, "BAC Visa")
    await _add_terms(session, user_id, card.id, payment_mode="full")
    await _charge(session, user_id, card.id, "-400000")

    result = await list_recurring_bills(status="upcoming", user_id=user_id)
    card_rows = [b for b in result["bills"] if b["category"] == "tarjeta"]
    assert len(card_rows) == 1
    assert Decimal(card_rows[0]["amount"]) == Decimal("400000")
