"""Phase 7b B5 — the card minimum as a first-class obligation.

- Feed: a card with terms + balance projects `item_type="card_payment"` from
  payment_due_day with the LIVE minimum; a paid-down card stops projecting.
- Reservation: an attached card reserves its minimum while unpaid this cycle;
  a transfer INTO the card ≥ the minimum releases it AND the stamped debit
  leg counts as envelope spend ("reservation swaps to spend, never both").
- Gate: an unattached card with a balance shows up in
  list_unattached_obligations with source="card"; attaching clears it.
- Chat attach: match_obligations_by_name resolves the card; the commit sets
  CreditCardTerms.envelope_id.
- Coexistence: a credit_card Debt LINKED to the account with terms is
  excluded from feed/gate (never both); an unlinked legacy card-debt keeps
  counting.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from api.models.account import Account
from api.models.credit_card_terms import CreditCardTerms
from api.models.debt import Debt
from api.models.envelope import Envelope
from api.models.transaction import Transaction
from api.models.user import User
from api.schemas.transfers import TransferCreate
from api.services.envelopes import (
    compute_envelope_summary,
    list_unattached_obligations,
    match_obligations_by_name,
)
from api.services.recurrence import get_upcoming_feed
from api.services.transfers import create_transfer_with_transactions


async def _get_user(session, user_id) -> User:
    return (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one()


async def _add_account(
    session, user_id, name, *, account_type="credit", initial_balance=Decimal("0")
):
    account = Account(
        user_id=user_id,
        name=name,
        account_type=account_type,
        currency="CRC",
        initial_balance=initial_balance,
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _add_terms(session, user_id, account_id, *, envelope_id=None):
    terms = CreditCardTerms(
        user_id=user_id,
        account_id=account_id,
        annual_interest_rate=Decimal("0.45"),
        minimum_payment_pct=Decimal("0.025"),
        minimum_payment_floor=Decimal("5000"),
        payment_due_day=10,
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


async def _add_envelope(session, user_id, name="Deudas", limit="100000"):
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
async def test_feed_projects_card_minimum_live(db_with_user):
    session, user_id = db_with_user
    card = await _add_account(session, user_id, "BAC Visa")
    await _add_terms(session, user_id, card.id)

    today = date.today()
    window = (today, today + timedelta(days=60))

    # Nothing owed → no projection.
    entries = await get_upcoming_feed(
        session, user_id, from_date=window[0], to_date=window[1]
    )
    assert [e for e in entries if e.item_type == "card_payment"] == []

    # Charge → the next due date projects with the live minimum.
    await _charge(session, user_id, card.id, "-400000")
    entries = await get_upcoming_feed(
        session, user_id, from_date=window[0], to_date=window[1]
    )
    card_entries = [e for e in entries if e.item_type == "card_payment"]
    assert len(card_entries) == 1
    entry = card_entries[0]
    assert entry.account_id == card.id
    assert entry.date.day == 10
    assert Decimal(str(entry.amount)) == Decimal("10000.00")  # 2.5% × 400k
    assert "BAC Visa" in entry.title


@pytest.mark.asyncio
async def test_reservation_reserves_then_swaps_to_spend_on_payment(db_with_user):
    session, user_id = db_with_user
    checking = await _add_account(
        session,
        user_id,
        "Corriente",
        account_type="checking",
        initial_balance=Decimal("100000"),  # cover the ₡15.000 payment below
    )
    card = await _add_account(session, user_id, "BAC Visa")
    env = await _add_envelope(session, user_id)
    await _add_terms(session, user_id, card.id, envelope_id=env.id)
    await _charge(session, user_id, card.id, "-400000")  # minimum = 10,000

    user = await _get_user(session, user_id)

    summary = await compute_envelope_summary(session, user=user)
    item = next(i for i in summary.envelopes if str(i.id) == str(env.id))
    assert Decimal(str(item.reserved)) == Decimal("10000.00")
    assert Decimal(str(item.spent)) == Decimal("0")

    # Pay the card ≥ minimum → reservation releases; the stamped debit leg
    # counts as envelope spend instead. Never both.
    await create_transfer_with_transactions(
        session,
        user_id=user_id,
        payload=TransferCreate(
            from_account_id=checking.id,
            to_account_id=card.id,
            amount=Decimal("15000"),
            currency="CRC",
        ),
    )
    await session.commit()

    summary = await compute_envelope_summary(session, user=user)
    item = next(i for i in summary.envelopes if str(i.id) == str(env.id))
    assert Decimal(str(item.reserved)) == Decimal("0")
    assert Decimal(str(item.spent)) == Decimal("15000")


@pytest.mark.asyncio
async def test_gate_names_unattached_card_and_attach_clears_it(db_with_user):
    session, user_id = db_with_user
    card = await _add_account(session, user_id, "Visa Promerica")
    terms = await _add_terms(session, user_id, card.id)
    await _charge(session, user_id, card.id, "-200000")

    unattached = await list_unattached_obligations(session, user_id=user_id)
    assert ("Visa Promerica", Decimal("5000.00"), "card") in unattached

    env = await _add_envelope(session, user_id)
    terms.envelope_id = env.id
    await session.commit()

    unattached = await list_unattached_obligations(session, user_id=user_id)
    assert [o for o in unattached if o[2] == "card"] == []


@pytest.mark.asyncio
async def test_chat_attach_resolves_card_and_commit_sets_envelope(db_with_user):
    from bot.commit import _commit_attach_expense
    from bot.pending import PendingAction

    session, user_id = db_with_user
    card = await _add_account(session, user_id, "BAC Visa")
    terms = await _add_terms(session, user_id, card.id)
    await _charge(session, user_id, card.id, "-200000")
    env = await _add_envelope(session, user_id)

    matches = await match_obligations_by_name(
        session, user_id=user_id, hint="visa"
    )
    assert len(matches) == 1
    assert matches[0].kind == "card"
    assert matches[0].id == card.id

    from api.redis_client import get_redis

    user = await _get_user(session, user_id)
    card_id, env_id, terms_id = card.id, env.id, terms.id
    await _commit_attach_expense(
        user=user,
        pending=PendingAction(
            short_id="t1",
            action_type="attach_expense",
            payload={
                "action_type": "attach_expense",
                "obligation_kind": "card",
                "obligation_id": str(card_id),
                "obligation_name": "BAC Visa",
                "envelope_id": str(env_id),
                "envelope_name": env.name,
            },
            summary_es="x",
        ),
        db=session,
        redis=get_redis(),
    )

    session.expire_all()
    fresh = (
        await session.execute(
            select(CreditCardTerms).where(CreditCardTerms.id == terms_id)
        )
    ).scalar_one()
    assert fresh.envelope_id == env_id


@pytest.mark.asyncio
async def test_coexistence_linked_card_debt_is_superseded(db_with_user):
    session, user_id = db_with_user
    card = await _add_account(session, user_id, "BAC Visa")
    await _add_terms(session, user_id, card.id)
    await _charge(session, user_id, card.id, "-200000")

    # Legacy representation: the same card also exists as a Debt LINKED to
    # the account → it must not double-count anywhere.
    linked = Debt(
        user_id=user_id,
        account_id=card.id,
        name="Visa BAC (deuda vieja)",
        debt_type="credit_card",
        original_amount=Decimal("200000"),
        current_balance=Decimal("200000"),
        interest_rate=Decimal("0.45"),
        minimum_payment=Decimal("9999"),
        payment_due_day=12,
    )
    # An UNLINKED legacy card-debt keeps working unchanged.
    unlinked = Debt(
        user_id=user_id,
        name="Tarjeta Davivienda (sin cuenta)",
        debt_type="credit_card",
        original_amount=Decimal("100000"),
        current_balance=Decimal("100000"),
        interest_rate=Decimal("0.40"),
        minimum_payment=Decimal("4000"),
        payment_due_day=20,
    )
    session.add_all([linked, unlinked])
    await session.commit()

    today = date.today()
    entries = await get_upcoming_feed(
        session, user_id, from_date=today, to_date=today + timedelta(days=60)
    )
    debt_titles = [e.title for e in entries if e.item_type == "debt"]
    assert "Visa BAC (deuda vieja)" not in debt_titles
    assert "Tarjeta Davivienda (sin cuenta)" in debt_titles
    assert any(e.item_type == "card_payment" for e in entries)

    unattached = await list_unattached_obligations(session, user_id=user_id)
    names = [name for name, _amt, _src in unattached]
    assert "Visa BAC (deuda vieja)" not in names
    assert "Tarjeta Davivienda (sin cuenta)" in names
    assert "BAC Visa" in names
