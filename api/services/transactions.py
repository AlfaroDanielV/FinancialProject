"""Transaction helpers used by the Telegram dispatcher and /undo.

Scope is deliberately narrow: create, safe-delete (for /undo), recent
listing, and windowed-sum (for balance queries). The existing REST router
continues to own its own CRUD paths and was NOT refactored as part of
this extraction.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.bill_occurrence import BillOccurrence
from ..models.debt import DebtPayment
from ..models.transaction import Transaction
from ..models.user import User


@dataclass
class UndoGuardError(Exception):
    """Raised when a /undo would violate a safety guard.

    `reason_code` is machine-readable so the bot can pick the right Spanish
    message without parsing human strings.
    """

    reason_code: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"undo_blocked:{self.reason_code}"


UNDO_REASON_NOT_FOUND = "not_found"
UNDO_REASON_WRONG_SOURCE = "wrong_source"
UNDO_REASON_LINKED_TO_BILL = "linked_to_bill"


# ── general hard delete (app "Eliminar definitivamente" + duplicate flow) ─────

TXN_DELETE_SHADOW = "shadow"
TXN_DELETE_TRANSFER_LEG = "transfer_leg"
TXN_DELETE_GOAL_FLOW = "goal_flow"
TXN_DELETE_LINKED_TO_BILL = "linked_to_bill"
TXN_DELETE_LINKED_TO_DEBT = "linked_to_debt"

# Voseo CR copy, shared by every delete surface (REST 409, bot reply, native).
TXN_DELETE_REASON_ES: dict[str, str] = {
    TXN_DELETE_SHADOW: (
        "Este movimiento está pendiente de aprobar. "
        "Aprobalo o rechazalo desde el bot."
    ),
    TXN_DELETE_TRANSFER_LEG: (
        "Es parte de una transferencia. Eliminá la transferencia, no el "
        "movimiento."
    ),
    TXN_DELETE_GOAL_FLOW: (
        "Este movimiento pertenece a una meta de ahorro. Gestionalo desde la "
        "meta."
    ),
    TXN_DELETE_LINKED_TO_BILL: (
        "Está ligado al pago de un gasto fijo. Desvinculá el pago antes de "
        "eliminarlo."
    ),
    TXN_DELETE_LINKED_TO_DEBT: (
        "Está ligado al pago de una deuda. Desvinculá el pago antes de "
        "eliminarlo."
    ),
}


@dataclass
class TransactionDeleteError(Exception):
    """Raised when a hard delete would violate an auditability guard.

    `reason_code` is machine-readable so each surface (REST 409, bot reply,
    native Alert) picks its own Spanish copy.
    """

    reason_code: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"txn_delete_blocked:{self.reason_code}"


async def hard_delete_transaction(
    db: AsyncSession, *, user: User, txn: Transaction
) -> None:
    """Permanently delete a transaction (no archive). FLUSHES, never commits —
    the caller owns the transaction boundary so the delete can compose with
    nudge cleanup / the duplicate flow in one commit.

    Guards (raise TransactionDeleteError, mirroring the PATCH immutability
    rules + the /undo bill guard so deleting can't silently orphan a payment):
      - shadow / pending_review rows → approve or reject via the bot.
      - transfer legs → delete the transfer instead.
      - goal flows → machine-managed, gestionar desde la meta.
      - linked to a paid bill occurrence / debt payment → desvincular primero
        (the FK is SET NULL, so deleting would leave the bill/debt marked paid
        with no backing movement — a silent ledger inconsistency).
    Archived rows ARE deletable (unlike PATCH, which blocks them).
    """
    if txn.status != "confirmed":
        raise TransactionDeleteError(TXN_DELETE_SHADOW)
    if txn.transfer_id is not None:
        raise TransactionDeleteError(TXN_DELETE_TRANSFER_LEG)
    if txn.goal_id is not None:
        raise TransactionDeleteError(TXN_DELETE_GOAL_FLOW)

    linked_bill = (
        await db.execute(
            select(func.count())
            .select_from(BillOccurrence)
            .where(BillOccurrence.transaction_id == txn.id)
        )
    ).scalar_one()
    if linked_bill > 0:
        raise TransactionDeleteError(TXN_DELETE_LINKED_TO_BILL)

    linked_debt = (
        await db.execute(
            select(func.count())
            .select_from(DebtPayment)
            .where(DebtPayment.transaction_id == txn.id)
        )
    ).scalar_one()
    if linked_debt > 0:
        raise TransactionDeleteError(TXN_DELETE_LINKED_TO_DEBT)

    await db.delete(txn)
    await db.flush()


async def create_transaction(
    *,
    user: User,
    amount: Decimal,
    currency: str,
    merchant: Optional[str],
    category: Optional[str],
    description: Optional[str],
    transaction_date: date,
    account_id: Optional[uuid.UUID],
    source: str,
    db: AsyncSession,
) -> Transaction:
    """Create and commit a transaction. Sign of `amount` is the caller's
    responsibility — negative for expenses, positive for income, matching
    the column convention.
    """
    txn = Transaction(
        user_id=user.id,
        account_id=account_id,
        amount=amount,
        currency=currency,
        merchant=merchant,
        description=description,
        category=category,
        transaction_date=transaction_date,
        source=source,
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)
    return txn


async def delete_telegram_transaction(
    *,
    user: User,
    transaction_id: uuid.UUID,
    db: AsyncSession,
) -> Transaction:
    """Hard-delete a bot-created transaction as part of /undo.

    Three guards (any failure raises UndoGuardError):
      1. Row must exist and belong to the caller.
      2. Row.source must be 'telegram' — we will not nuke manual or
         shortcut-created rows even if the Redis key somehow points at one.
      3. No bill_occurrence.transaction_id may reference this row — the
         user already used it to mark a bill paid; reversing would corrupt
         that linkage. Ask them to un-mark the bill first.

    Returns the deleted row (detached) for logging.
    """
    result = await db.execute(
        select(Transaction).where(
            Transaction.id == transaction_id,
            Transaction.user_id == user.id,
        )
    )
    txn = result.scalar_one_or_none()
    if txn is None:
        raise UndoGuardError(UNDO_REASON_NOT_FOUND)
    if txn.source != "telegram":
        raise UndoGuardError(UNDO_REASON_WRONG_SOURCE)

    linked = await db.execute(
        select(func.count())
        .select_from(BillOccurrence)
        .where(BillOccurrence.transaction_id == transaction_id)
    )
    if linked.scalar_one() > 0:
        raise UndoGuardError(UNDO_REASON_LINKED_TO_BILL)

    await db.delete(txn)
    await db.commit()
    return txn


async def recent_for_user(
    *, user: User, limit: int, db: AsyncSession
) -> list[Transaction]:
    result = await db.execute(
        select(Transaction)
        .where(Transaction.user_id == user.id)
        .order_by(
            Transaction.transaction_date.desc(),
            Transaction.created_at.desc(),
        )
        .limit(limit)
    )
    return list(result.scalars().all())


async def sum_in_window(
    *,
    user: User,
    start: date,
    end: date,
    db: AsyncSession,
) -> dict[str, Decimal]:
    """Sum expenses (amount < 0) and income (amount > 0) per currency for
    the inclusive [start, end] window.

    Returned dict has keys like `"CRC_expense"`, `"CRC_income"`,
    `"USD_expense"`, etc. Missing keys mean no rows for that currency/side.
    Expenses are returned as positive Decimal for display — the sign is
    implied by the key.
    """
    expense_rows = await db.execute(
        select(Transaction.currency, func.sum(Transaction.amount))
        .where(
            Transaction.user_id == user.id,
            Transaction.transaction_date >= start,
            Transaction.transaction_date <= end,
            Transaction.amount < 0,
        )
        .group_by(Transaction.currency)
    )
    income_rows = await db.execute(
        select(Transaction.currency, func.sum(Transaction.amount))
        .where(
            Transaction.user_id == user.id,
            Transaction.transaction_date >= start,
            Transaction.transaction_date <= end,
            Transaction.amount > 0,
        )
        .group_by(Transaction.currency)
    )

    out: dict[str, Decimal] = {}
    for currency, total in expense_rows.all():
        out[f"{currency}_expense"] = abs(total)
    for currency, total in income_rows.all():
        out[f"{currency}_income"] = total
    return out


def window_bounds(window: str, today: date) -> tuple[date, date]:
    """Resolve a natural-language window string from ExtractionResult into
    a concrete [start, end] pair.

    Accepted: today | yesterday | this_week | this_month | last_n_days:<n>
    Unknown values collapse to today..today so the caller never crashes on
    a model hallucination — the bot will just report zero.
    """
    if window == "today":
        return today, today
    if window == "yesterday":
        y = today - timedelta(days=1)
        return y, y
    if window == "this_week":
        # Monday as week start, matching Costa Rican convention.
        start = today - timedelta(days=today.weekday())
        return start, today
    if window == "this_month":
        return today.replace(day=1), today
    if window.startswith("last_n_days:"):
        try:
            n = int(window.split(":", 1)[1])
            if n < 1:
                n = 1
            if n > 365:
                n = 365
            return today - timedelta(days=n - 1), today
        except ValueError:
            pass
    return today, today
