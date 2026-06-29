"""Register a positive credit-account movement AS a card payment.

A Gmail/manual payment mis-marked as income on the card is converted into a real
transfer from a chosen fund account → the card, and the original row is deleted.
The source account is debited (the money actually leaves it), the card is
credited once (via the transfer leg), and transfer legs are excluded from income
— so the inflated income is gone and the payment is recognized.

Reuses `create_transfer_with_transactions` (funds guard, card-envelope stamping)
+ `hard_delete_transaction`.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api.models.account import Account
from api.models.transaction import Transaction
from api.models.transfer import Transfer
from api.models.user import User
from api.services.accounts import compute_account_balances
from api.services.transactions import (
    RegisterPaymentError,
    register_income_as_card_payment,
)


async def _user(session, user_id) -> User:
    return (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one()


async def _account(session, user_id, *, name, account_type, initial="0"):
    acc = Account(
        user_id=user_id,
        name=name,
        account_type=account_type,
        currency="CRC",
        initial_balance=Decimal(initial),
    )
    session.add(acc)
    await session.commit()
    await session.refresh(acc)
    return acc


async def _txn(session, user_id, account_id, amount, **kw):
    txn = Transaction(
        user_id=user_id,
        account_id=account_id,
        amount=Decimal(amount),
        currency="CRC",
        transaction_date=date.today(),
        source=kw.pop("source", "gmail"),
        status=kw.pop("status", "confirmed"),
        **kw,
    )
    session.add(txn)
    await session.commit()
    await session.refresh(txn)
    return txn


@pytest.mark.asyncio
async def test_converts_income_to_transfer_and_deletes_row(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    card = await _account(session, user_id, name="BAC Visa", account_type="credit")
    fund = await _account(
        session, user_id, name="BAC Cuenta", account_type="checking", initial="100000"
    )
    income = await _txn(session, user_id, card.id, "50000")  # mis-marked payment

    result = await register_income_as_card_payment(
        session, user=user, txn=income, source_account_id=fund.id
    )
    await session.commit()

    # The original positive row is gone.
    gone = (
        await session.execute(
            select(Transaction).where(Transaction.id == income.id)
        )
    ).scalar_one_or_none()
    assert gone is None

    # A transfer with two legs exists, both tagged with transfer_id.
    transfer = (
        await session.execute(select(Transfer).where(Transfer.id == result.transfer.id))
    ).scalar_one()
    legs = (
        await session.execute(
            select(Transaction).where(Transaction.transfer_id == transfer.id)
        )
    ).scalars().all()
    assert len(legs) == 2

    # The fund account was debited; the card net is unchanged (income → leg).
    balances = await compute_account_balances(
        session, user_id=user_id, account_ids=[fund.id, card.id]
    )
    assert balances[fund.id].current == Decimal("50000")  # 100k − 50k
    assert balances[card.id].current == Decimal("50000")  # the credit leg


@pytest.mark.asyncio
async def test_amount_override(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    card = await _account(session, user_id, name="BAC Visa", account_type="credit")
    fund = await _account(
        session, user_id, name="BAC Cuenta", account_type="checking", initial="100000"
    )
    income = await _txn(session, user_id, card.id, "50000")

    await register_income_as_card_payment(
        session, user=user, txn=income, source_account_id=fund.id,
        amount=Decimal("30000"),
    )
    await session.commit()

    balances = await compute_account_balances(
        session, user_id=user_id, account_ids=[fund.id, card.id]
    )
    assert balances[fund.id].current == Decimal("70000")  # 100k − 30k


@pytest.mark.asyncio
async def test_rejects_non_credit_account(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    checking = await _account(
        session, user_id, name="Cuenta", account_type="checking", initial="0"
    )
    fund = await _account(
        session, user_id, name="Otra", account_type="checking", initial="100000"
    )
    income = await _txn(session, user_id, checking.id, "50000")

    with pytest.raises(RegisterPaymentError) as exc:
        await register_income_as_card_payment(
            session, user=user, txn=income, source_account_id=fund.id
        )
    assert exc.value.reason_code == "not_credit"


@pytest.mark.asyncio
async def test_rejects_expense_row(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    card = await _account(session, user_id, name="BAC Visa", account_type="credit")
    fund = await _account(
        session, user_id, name="BAC Cuenta", account_type="checking", initial="100000"
    )
    charge = await _txn(session, user_id, card.id, "-50000")  # a purchase

    with pytest.raises(RegisterPaymentError) as exc:
        await register_income_as_card_payment(
            session, user=user, txn=charge, source_account_id=fund.id
        )
    assert exc.value.reason_code == "not_income"


@pytest.mark.asyncio
async def test_rejects_shadow_row(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    card = await _account(session, user_id, name="BAC Visa", account_type="credit")
    fund = await _account(
        session, user_id, name="BAC Cuenta", account_type="checking", initial="100000"
    )
    income = await _txn(session, user_id, card.id, "50000", status="shadow")

    with pytest.raises(RegisterPaymentError) as exc:
        await register_income_as_card_payment(
            session, user=user, txn=income, source_account_id=fund.id
        )
    assert exc.value.reason_code == "shadow"


@pytest.mark.asyncio
async def test_insufficient_funds_raises_http_400(db_with_user):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    card = await _account(session, user_id, name="BAC Visa", account_type="credit")
    fund = await _account(
        session, user_id, name="BAC Cuenta", account_type="checking", initial="10000"
    )
    income = await _txn(session, user_id, card.id, "50000")

    with pytest.raises(HTTPException) as exc:  # the transfer service funds guard
        await register_income_as_card_payment(
            session, user=user, txn=income, source_account_id=fund.id
        )
    assert exc.value.status_code == 400
