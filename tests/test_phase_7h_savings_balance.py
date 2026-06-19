"""Phase 7h — savings is "plata apartada", excluded from the available total.

The home screen's confusing figure summed savings + checking. Now the
dashboard summary reports `available_balance` (spending accounts, savings
EXCLUDED) and `savings_balance` separately; `balance_total` stays for
back-compat. A checking→savings transfer must lower available and raise
savings (transfers net across accounts).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from api.models.account import Account
from api.models.transaction import Transaction
from api.models.user import User
from api.services.dashboard.summary import get_dashboard_summary


def _today_iso_day(day: int = 15) -> date:
    t = date.today()
    return date(t.year, t.month, min(day, 28))


async def _mk_account(session, user_id, *, name, account_type, initial):
    acc = Account(
        user_id=user_id,
        name=name,
        account_type=account_type,
        currency="CRC",
        initial_balance=Decimal(initial),
    )
    session.add(acc)
    await session.flush()
    return acc.id


def _tx(user_id, account_id, amount):
    return Transaction(
        user_id=user_id,
        account_id=account_id,
        amount=Decimal(amount),
        currency="CRC",
        transaction_date=_today_iso_day(),
        source="manual",
        status="confirmed",
        archived=False,
    )


@pytest.mark.asyncio
async def test_savings_excluded_from_available_balance(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    checking = await _mk_account(
        session, user_id, name="BAC Corriente", account_type="checking", initial="100000"
    )
    savings = await _mk_account(
        session, user_id, name="Ahorros", account_type="savings", initial="500000"
    )
    session.add_all(
        [
            _tx(user_id, checking, "-20000"),  # spend from checking
            _tx(user_id, savings, "50000"),    # deposit into savings
        ]
    )
    await session.commit()

    summary = await get_dashboard_summary(
        db=session, user=user, period="month_current"
    )

    # available = checking only: 100000 − 20000
    assert summary.available_balance == Decimal("80000")
    # savings shown separately: 500000 + 50000
    assert summary.savings_balance == Decimal("550000")
    # balance_total still the grand sum (back-compat)
    assert summary.balance_total == Decimal("630000")


@pytest.mark.asyncio
async def test_no_savings_account_means_zero_savings(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    checking = await _mk_account(
        session, user_id, name="Efectivo", account_type="checking", initial="40000"
    )
    session.add(_tx(user_id, checking, "10000"))
    await session.commit()

    summary = await get_dashboard_summary(
        db=session, user=user, period="month_current"
    )
    assert summary.available_balance == Decimal("50000")
    assert summary.savings_balance == Decimal("0")
