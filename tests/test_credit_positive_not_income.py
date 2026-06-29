"""A positive amount on a credit account is a card payment/refund, never income.

The mis-marked "ingreso en la tarjeta" must not inflate reported income. The
rule lives in `api/services/income_rules.py::not_card_payment_income` and is
applied by every income aggregation (the dashboard summary + the chat tools).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from api.models.account import Account
from api.models.transaction import Transaction
from api.models.user import User
from api.services.dashboard.summary import get_dashboard_summary
from api.services.income_rules import not_card_payment_income


async def _user(session, user_id) -> User:
    return (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one()


async def _account(session, user_id, *, name, account_type):
    acc = Account(
        user_id=user_id,
        name=name,
        account_type=account_type,
        currency="CRC",
        initial_balance=Decimal("0"),
    )
    session.add(acc)
    await session.commit()
    await session.refresh(acc)
    return acc


async def _txn(session, user_id, account_id, amount):
    session.add(
        Transaction(
            user_id=user_id,
            account_id=account_id,
            amount=Decimal(amount),
            currency="CRC",
            transaction_date=date.today(),
            source="manual",
            status="confirmed",
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_dashboard_income_excludes_credit_positive(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    checking = await _account(
        session, user_id, name="Cuenta", account_type="checking"
    )
    card = await _account(session, user_id, name="BAC Visa", account_type="credit")
    await _txn(session, user_id, checking.id, "100000")   # real income
    await _txn(session, user_id, card.id, "50000")        # mis-marked payment
    await _txn(session, user_id, checking.id, "-20000")   # a real expense

    summary = await get_dashboard_summary(
        session, user=user, period="month_current"
    )
    assert summary.income_total == Decimal("100000")  # the credit +50k excluded
    assert summary.expense_total == Decimal("20000")


@pytest.mark.asyncio
async def test_income_clause_excludes_credit_rows(db_with_user):
    # Validates the shared clause the chat income tools use.
    session, user_id = db_with_user
    checking = await _account(
        session, user_id, name="Cuenta", account_type="checking"
    )
    card = await _account(session, user_id, name="BAC Visa", account_type="credit")
    await _txn(session, user_id, checking.id, "100000")
    await _txn(session, user_id, card.id, "50000")

    total = (
        await session.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                Transaction.user_id == user_id,
                Transaction.amount > 0,
                not_card_payment_income(user_id),
            )
        )
    ).scalar_one()
    assert Decimal(total) == Decimal("100000")  # credit +50k excluded
