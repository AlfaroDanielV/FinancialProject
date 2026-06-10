"""B2 — compute-live envelope reservations for attached fixed expenses.

An attached recurring bill / debt reserves its expected amount inside the
envelope WHILE UNPAID this cycle; the reservation releases once the actual
payment lands (the bill's current-month occurrence is paid, or a DebtPayment
exists this month). Variable bills reserve 0; detaching restores `available`.
No stored balance — `reserved`/`available` are computed live in
`compute_envelope_summary`, like `spent`.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from api.models.bill_occurrence import BillOccurrence
from api.models.debt import Debt, DebtPayment
from api.models.envelope import Envelope
from api.models.recurring_bill import RecurringBill
from api.models.transaction import Transaction
from api.models.user import User
from api.services.envelopes import compute_envelope_summary


def _month_start() -> date:
    t = date.today()
    return date(t.year, t.month, 1)


def _env(user_id, *, name="Servicios", limit="200000"):
    return Envelope(
        user_id=user_id, name=name, envelope_class="needs",
        limit_amount=Decimal(limit), currency="CRC",
    )


def _bill(user_id, env_id, *, name="ICE", amount="30000", variable=False):
    return RecurringBill(
        user_id=user_id, name=name, category="servicios",
        amount_expected=None if variable else Decimal(amount),
        is_variable_amount=variable, currency="CRC", frequency="monthly",
        start_date=date.today(), envelope_id=env_id,
    )


def _debt(user_id, env_id, *, name="Préstamo", payment="100000"):
    return Debt(
        user_id=user_id, name=name, debt_type="personal_loan",
        original_amount=Decimal("3000000"), current_balance=Decimal("2000000"),
        interest_rate=Decimal("0.18"), minimum_payment=Decimal(payment),
        payment_due_day=1, envelope_id=env_id,
    )


async def _item(session, user, env_id):
    summary = await compute_envelope_summary(session, user=user)
    return next(i for i in summary.envelopes if i.id == env_id)


@pytest.mark.asyncio
async def test_unpaid_attached_bill_reserves_amount(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    env = _env(user_id)
    session.add(env)
    await session.flush()
    session.add(_bill(user_id, env.id))
    await session.commit()

    item = await _item(session, user, env.id)
    assert item.reserved == 30000.0
    assert item.available == 170000.0  # 200k − 30k reserved − 0 spent
    assert item.spent == 0.0


@pytest.mark.asyncio
async def test_variable_bill_reserves_zero(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    env = _env(user_id)
    session.add(env)
    await session.flush()
    session.add(_bill(user_id, env.id, name="Agua", variable=True))
    await session.commit()

    item = await _item(session, user, env.id)
    assert item.reserved == 0.0
    assert item.available == 200000.0


@pytest.mark.asyncio
async def test_paid_bill_releases_reservation(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    env = _env(user_id)
    session.add(env)
    await session.flush()
    bill = _bill(user_id, env.id)
    session.add(bill)
    await session.flush()
    # current-month occurrence, PAID → reservation releases.
    session.add(
        BillOccurrence(
            user_id=user_id, recurring_bill_id=bill.id, due_date=_month_start(),
            amount_expected=Decimal("30000"), amount_paid=Decimal("30000"),
            status="paid",
        )
    )
    await session.commit()

    item = await _item(session, user, env.id)
    assert item.reserved == 0.0


@pytest.mark.asyncio
async def test_paid_bill_counts_actual_spend_not_reservation(db_with_user):
    """The 'never both' contract: once paid, the reservation releases AND the
    actual payment (tagged to the envelope on mark-paid) counts as spend."""
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    env = _env(user_id)
    session.add(env)
    await session.flush()
    bill = _bill(user_id, env.id)
    session.add(bill)
    await session.flush()
    session.add(
        BillOccurrence(
            user_id=user_id, recurring_bill_id=bill.id, due_date=_month_start(),
            amount_expected=Decimal("30000"), amount_paid=Decimal("28000"),
            status="paid",
        )
    )
    # the actual payment txn, tagged to the envelope (propagated on mark-paid).
    session.add(
        Transaction(
            user_id=user_id, amount=Decimal("-28000"), currency="CRC",
            transaction_date=date.today(), source="manual", status="confirmed",
            envelope_id=env.id,
        )
    )
    await session.commit()

    item = await _item(session, user, env.id)
    assert item.reserved == 0.0  # released
    assert item.spent == 28000.0  # actual counts once
    assert item.available == 172000.0  # 200k − 0 reserved − 28k spent


@pytest.mark.asyncio
async def test_unpaid_attached_debt_reserves_minimum(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    env = _env(user_id, name="Deudas", limit="300000")
    session.add(env)
    await session.flush()
    session.add(_debt(user_id, env.id))
    await session.commit()

    item = await _item(session, user, env.id)
    assert item.reserved == 100000.0
    assert item.available == 200000.0


@pytest.mark.asyncio
async def test_paid_debt_releases_reservation(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    env = _env(user_id, name="Deudas", limit="300000")
    session.add(env)
    await session.flush()
    debt = _debt(user_id, env.id)
    session.add(debt)
    await session.flush()
    session.add(
        DebtPayment(
            debt_id=debt.id, payment_date=date.today(),
            amount_paid=100000.0, remaining_balance=1900000.0,
        )
    )
    await session.commit()

    item = await _item(session, user, env.id)
    assert item.reserved == 0.0


@pytest.mark.asyncio
async def test_detach_restores_available(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    env = _env(user_id)
    session.add(env)
    await session.flush()
    bill = _bill(user_id, env.id)
    session.add(bill)
    await session.commit()

    item = await _item(session, user, env.id)
    assert item.reserved == 30000.0

    bill.envelope_id = None  # detach
    await session.commit()

    item2 = await _item(session, user, env.id)
    assert item2.reserved == 0.0
    assert item2.available == 200000.0
