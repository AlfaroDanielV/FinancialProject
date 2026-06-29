"""Shared debt-payment recording (REST router + chat commit).

Writes a ``DebtPayment`` row, lowers ``Debt.current_balance``, and bumps
``payments_made``. A debt payment moves the DEBT's balance, not a bank account
— there is no account-side transaction (operator decision, Flexible Payment
Dates). The amortization schedule is a read-time projection and is NEVER
mutated here.

The function ``flush``es but does not ``commit`` — the caller owns the
transaction boundary and the best-effort paid-off celebration.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.debt import Debt, DebtPayment
from ..models.transaction import Transaction


async def record_debt_payment(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    debt: Debt,
    amount_paid: float,
    payment_date: date,
    notes: Optional[str] = None,
    transaction_id: Optional[uuid.UUID] = None,
    remaining_balance: Optional[float] = None,
    principal_portion: Optional[float] = None,
    interest_portion: Optional[float] = None,
    extra_payment: Optional[float] = None,
) -> DebtPayment:
    """Record a payment against ``debt``. ``remaining_balance`` auto-derives
    from the live balance when not supplied (clamped to 0)."""
    remaining = remaining_balance
    if remaining is None:
        remaining = float(debt.current_balance) - amount_paid
        if remaining < 0:
            remaining = 0

    payment = DebtPayment(
        debt_id=debt.id,
        transaction_id=transaction_id,
        payment_date=payment_date,
        amount_paid=amount_paid,
        principal_portion=principal_portion,
        interest_portion=interest_portion,
        extra_payment=extra_payment,
        remaining_balance=remaining,
        notes=notes,
    )
    db.add(payment)

    # Fixed-expense attachment: if the debt is attached to an envelope and this
    # payment links a transaction, tag that transaction to the envelope so the
    # actual payment counts as spend there and the reservation releases the same
    # cycle (never both).
    if transaction_id is not None and debt.envelope_id is not None:
        txn = (
            await db.execute(
                select(Transaction).where(
                    Transaction.id == transaction_id,
                    Transaction.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if txn is not None and txn.envelope_id is None:
            txn.envelope_id = debt.envelope_id

    debt.current_balance = remaining
    debt.payments_made = (debt.payments_made or 0) + 1
    await db.flush()
    return payment


async def undo_debt_payment(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    debt_payment_id: uuid.UUID,
) -> bool:
    """Reverse a chat ``record_debt_payment`` (Flexible Payment Dates): add the
    paid amount back to the debt's ``current_balance`` (and decrement
    ``payments_made``), then delete the ``DebtPayment`` row. Returns False when
    the payment is gone or not the caller's — nothing to undo.

    Restoring by ``+ amount_paid`` is exact for the immediate-undo case (/undo
    reverses the LAST action, so current_balance still equals this payment's
    ``remaining``). The chat dispatcher rejects an overpayment (amount > balance),
    so the chat path never produces a clamped ``remaining`` that this formula
    couldn't recover."""
    payment = (
        await db.execute(
            select(DebtPayment).where(DebtPayment.id == debt_payment_id)
        )
    ).scalar_one_or_none()
    if payment is None:
        return False
    debt = (
        await db.execute(
            select(Debt).where(
                Debt.id == payment.debt_id, Debt.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if debt is None:
        return False

    debt.current_balance = float(debt.current_balance) + float(payment.amount_paid)
    debt.payments_made = max((debt.payments_made or 1) - 1, 0)
    await db.delete(payment)
    await db.commit()
    return True
