"""Credit-card statement-cycle aware upcoming payment.

A card with `statement_day` set surfaces the balance owed AT the last corte, due
on the following `payment_due_day`. The feed shows the corte TOTAL (never the
minimum), marks it settled per `payment_mode` (contado → the whole corte balance;
minimum → the minimum), and stops projecting once settled — so purchases after
the corte don't keep a paid statement on the feed. The corte balance is derived
live (the reconciliation anchor when present, else the ledger as-of the corte).

Covers `card_statement_status` (the cycle status) and the `get_upcoming_feed`
wiring (corte total shown, suppressed when settled). The pure date/settlement
helpers live in `app/domain/credit/statement_cycle.py`.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from api.models.account import Account
from api.models.account_anchor import AccountAnchor
from api.models.credit_card_terms import CreditCardTerms
from api.models.transaction import Transaction
from api.services.credit_cards import (
    card_statement_status,
    list_active_cards_with_terms,
)
from api.services.recurrence import get_upcoming_feed

# Fixed reference date so corte/due math is deterministic (corte = 2026-06-19).
TODAY = date(2026, 6, 28)


async def _card(
    session,
    user_id,
    *,
    payment_mode="full",
    statement_day=19,
    payment_due_day=30,
    first_due_date=None,
    initial_balance="0",
):
    acc = Account(
        user_id=user_id,
        name="BAC Visa",
        account_type="credit",
        currency="CRC",
        initial_balance=Decimal(initial_balance),
    )
    session.add(acc)
    await session.commit()
    await session.refresh(acc)
    terms = CreditCardTerms(
        user_id=user_id,
        account_id=acc.id,
        annual_interest_rate=Decimal("0.45"),
        minimum_payment_pct=Decimal("0.025"),
        minimum_payment_floor=Decimal("5000"),
        statement_day=statement_day,
        payment_due_day=payment_due_day,
        payment_mode=payment_mode,
        first_due_date=first_due_date,
    )
    session.add(terms)
    await session.commit()
    return acc


async def _txn(session, user_id, account_id, amount, d):
    session.add(
        Transaction(
            user_id=user_id,
            account_id=account_id,
            amount=Decimal(amount),
            currency="CRC",
            transaction_date=d,
            source="manual",
            status="confirmed",
        )
    )
    await session.commit()


async def _status(session, user_id, *, today=TODAY):
    cards = await list_active_cards_with_terms(session, user_id=user_id)
    assert len(cards) == 1
    return await card_statement_status(session, card=cards[0], today=today)


@pytest.mark.asyncio
async def test_unsettled_full_from_ledger(db_with_user):
    session, user_id = db_with_user
    card = await _card(session, user_id, payment_mode="full")
    await _txn(session, user_id, card.id, "-100000", date(2026, 6, 15))  # ≤ corte

    st = await _status(session, user_id)
    assert st is not None
    assert st.corte == date(2026, 6, 19)
    assert st.due_date == date(2026, 6, 30)
    assert st.statement_balance == Decimal("100000")
    assert st.paid_since_corte == Decimal("0")
    assert st.remaining == Decimal("100000")
    assert st.settled is False


@pytest.mark.asyncio
async def test_settled_full_after_paying_corte(db_with_user):
    session, user_id = db_with_user
    card = await _card(session, user_id, payment_mode="full")
    await _txn(session, user_id, card.id, "-100000", date(2026, 6, 15))
    await _txn(session, user_id, card.id, "100000", date(2026, 6, 25))  # pago

    st = await _status(session, user_id)
    assert st.settled is True
    assert st.remaining == Decimal("0")


@pytest.mark.asyncio
async def test_partial_payment_not_settled_full(db_with_user):
    session, user_id = db_with_user
    card = await _card(session, user_id, payment_mode="full")
    await _txn(session, user_id, card.id, "-100000", date(2026, 6, 15))
    await _txn(session, user_id, card.id, "60000", date(2026, 6, 25))

    st = await _status(session, user_id)
    assert st.settled is False
    assert st.remaining == Decimal("40000")


@pytest.mark.asyncio
async def test_minimum_mode_settles_at_minimum_but_shows_total(db_with_user):
    session, user_id = db_with_user
    card = await _card(session, user_id, payment_mode="minimum")
    await _txn(session, user_id, card.id, "-100000", date(2026, 6, 15))
    await _txn(session, user_id, card.id, "5000", date(2026, 6, 25))  # = mínimo

    st = await _status(session, user_id)
    assert st.minimum == Decimal("5000")
    assert st.settled is True  # paid ≥ minimum
    # The surfaced remaining is still the corte total minus paid, NOT the minimum.
    assert st.remaining == Decimal("95000")


@pytest.mark.asyncio
async def test_post_corte_charges_excluded_from_statement(db_with_user):
    session, user_id = db_with_user
    card = await _card(session, user_id, payment_mode="full")
    await _txn(session, user_id, card.id, "-100000", date(2026, 6, 15))  # statement
    await _txn(session, user_id, card.id, "-50000", date(2026, 6, 22))   # next cycle
    await _txn(session, user_id, card.id, "100000", date(2026, 6, 25))   # pago

    st = await _status(session, user_id)
    assert st.statement_balance == Decimal("100000")  # the 50k post-corte excluded
    assert st.settled is True


@pytest.mark.asyncio
async def test_reconciliation_anchor_is_the_statement_balance(db_with_user):
    session, user_id = db_with_user
    card = await _card(session, user_id, payment_mode="full")
    # The user reconciled the statement: anchor at corte = −(owed at corte).
    session.add(
        AccountAnchor(
            user_id=user_id,
            account_id=card.id,
            value=Decimal("-100000"),
            currency="CRC",
            effective_date=date(2026, 6, 19),
            source="statement",
        )
    )
    await session.commit()
    await _txn(session, user_id, card.id, "-50000", date(2026, 6, 22))  # next cycle
    await _txn(session, user_id, card.id, "100000", date(2026, 6, 25))  # pago

    st = await _status(session, user_id)
    assert st.statement_balance == Decimal("100000")  # from the anchor, exact
    assert st.settled is True


@pytest.mark.asyncio
async def test_no_statement_day_returns_none(db_with_user):
    session, user_id = db_with_user
    card = await _card(session, user_id, payment_mode="full", statement_day=None)
    await _txn(session, user_id, card.id, "-100000", date(2026, 6, 15))

    st = await _status(session, user_id)
    assert st is None  # caller falls back to the live projection


@pytest.mark.asyncio
async def test_feed_shows_corte_total_not_minimum(db_with_user):
    session, user_id = db_with_user
    today = date.today()
    # statement_day = today → corte = today; the charge today is in the statement.
    card = await _card(
        session, user_id, payment_mode="minimum", statement_day=today.day
    )
    # payment_due_day defaulted to 30 in _card; override so due lands in-window.
    terms = (
        await session.execute(
            select(CreditCardTerms).where(CreditCardTerms.account_id == card.id)
        )
    ).scalar_one()
    terms.payment_due_day = today.day
    await session.commit()
    await _txn(session, user_id, card.id, "-400000", today)

    entries = await get_upcoming_feed(
        session, user_id, from_date=today, to_date=today + timedelta(days=60)
    )
    card_entries = [e for e in entries if e.item_type == "card_payment"]
    assert len(card_entries) == 1
    # The corte total (400k), NOT the minimum (10k), even in minimum mode.
    assert Decimal(str(card_entries[0].amount)) == Decimal("400000")


@pytest.mark.asyncio
async def test_feed_shows_overdue_unpaid_statement(db_with_user, monkeypatch):
    session, user_id = db_with_user
    # Pin "today" so the corte's due lands in the past (overdue), deterministically.
    import api.services.recurrence as rec

    monkeypatch.setattr(rec, "today_cr", lambda: TODAY)  # 2026-06-28
    # corte = 1 jun, due = 5 jun (both < today) → overdue unpaid statement.
    card = await _card(session, user_id, payment_mode="full", statement_day=1)
    terms = (
        await session.execute(
            select(CreditCardTerms).where(CreditCardTerms.account_id == card.id)
        )
    ).scalar_one()
    terms.payment_due_day = 5
    await session.commit()
    await _txn(session, user_id, card.id, "-100000", date(2026, 6, 1))

    entries = await get_upcoming_feed(
        session,
        user_id,
        from_date=TODAY,
        to_date=TODAY + timedelta(days=60),
        include_overdue=True,
    )
    card_entries = [e for e in entries if e.item_type == "card_payment"]
    assert len(card_entries) == 1
    assert card_entries[0].is_overdue is True
    assert Decimal(str(card_entries[0].amount)) == Decimal("100000")

    # Without include_overdue the past-due statement is not surfaced.
    entries_no = await get_upcoming_feed(
        session,
        user_id,
        from_date=TODAY,
        to_date=TODAY + timedelta(days=60),
        include_overdue=False,
    )
    assert [e for e in entries_no if e.item_type == "card_payment"] == []


@pytest.mark.asyncio
async def test_first_due_date_clamps_phantom_overdue(db_with_user):
    # A card opened mid-cycle: statement_day 20, due day 28, today Jul 1.
    # last_corte(20, Jul 1) = Jun 20 → natural due Jun 28 (a phantom past-due
    # that never actually existed). first_due_date = Jul 28 clamps it forward.
    session, user_id = db_with_user
    today = date(2026, 7, 1)
    card = await _card(
        session,
        user_id,
        payment_mode="full",
        statement_day=20,
        payment_due_day=28,
        first_due_date=date(2026, 7, 28),
        initial_balance="-100000",  # owed at creation
    )
    st = await _status(session, user_id, today=today)
    assert st is not None
    assert st.due_date == date(2026, 7, 28)  # clamped, NOT the phantom Jun 28
    assert st.remaining == Decimal("100000")


@pytest.mark.asyncio
async def test_no_first_due_date_keeps_natural_due(db_with_user):
    # NULL first_due_date → unchanged behavior (the phantom Jun 28 still shows).
    session, user_id = db_with_user
    today = date(2026, 7, 1)
    card = await _card(
        session,
        user_id,
        payment_mode="full",
        statement_day=20,
        payment_due_day=28,
        first_due_date=None,
        initial_balance="-100000",
    )
    st = await _status(session, user_id, today=today)
    assert st.due_date == date(2026, 6, 28)


@pytest.mark.asyncio
async def test_feed_no_overdue_when_first_due_clamped(db_with_user, monkeypatch):
    # End to end: the clamp removes the phantom overdue from the home feed.
    session, user_id = db_with_user
    import api.services.recurrence as rec

    today = date(2026, 7, 1)
    monkeypatch.setattr(rec, "today_cr", lambda: today)
    card = await _card(
        session,
        user_id,
        payment_mode="full",
        statement_day=20,
        payment_due_day=28,
        first_due_date=date(2026, 7, 28),
        initial_balance="-100000",
    )
    entries = await get_upcoming_feed(
        session,
        user_id,
        from_date=today,
        to_date=today + timedelta(days=60),
        include_overdue=True,
    )
    card_entries = [e for e in entries if e.item_type == "card_payment"]
    assert len(card_entries) == 1
    assert card_entries[0].is_overdue is False  # Jul 28 > Jul 1
    assert Decimal(str(card_entries[0].amount)) == Decimal("100000")


@pytest.mark.asyncio
async def test_feed_suppressed_when_settled(db_with_user):
    session, user_id = db_with_user
    today = date.today()
    card = await _card(
        session, user_id, payment_mode="full", statement_day=today.day
    )
    await _txn(session, user_id, card.id, "-400000", today)             # corte
    await _txn(session, user_id, card.id, "400000", today + timedelta(days=1))  # pago

    entries = await get_upcoming_feed(
        session, user_id, from_date=today, to_date=today + timedelta(days=60)
    )
    card_entries = [e for e in entries if e.item_type == "card_payment"]
    assert card_entries == []  # settled → no upcoming card payment
