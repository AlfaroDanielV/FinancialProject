"""Transaction helpers used by the Telegram dispatcher and /undo.

Scope is deliberately narrow: create, safe-delete (for /undo), recent
listing, and windowed-sum (for balance queries). The existing REST router
continues to own its own CRUD paths and was NOT refactored as part of
this extraction.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.bill_occurrence import BillOccurrence
from ..models.debt import DebtPayment
from ..models.envelope import Envelope
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


# ── period expense breakdown (chat /resumen table) ────────────────────────────


@dataclass
class PeriodExpenseRow:
    amount: Decimal
    currency: str
    category: Optional[str]
    txn_date: date
    envelope_name: Optional[str]


async def period_expense_breakdown(
    db: AsyncSession, *, user_id: uuid.UUID, start: date, end: date
) -> list[PeriodExpenseRow]:
    """Confirmed, non-archived expenses (amount < 0, not transfer/goal legs) in
    [start, end] inclusive, each with its envelope name (None if unassigned),
    newest first. Deterministic — powers the chat /resumen table."""
    stmt = (
        select(
            Transaction.amount,
            Transaction.currency,
            Transaction.category,
            Transaction.transaction_date,
            Envelope.name,
        )
        .select_from(Transaction)
        .outerjoin(Envelope, Transaction.envelope_id == Envelope.id)
        .where(
            Transaction.user_id == user_id,
            Transaction.transaction_date >= start,
            Transaction.transaction_date <= end,
            Transaction.amount < 0,
            Transaction.status == "confirmed",
            Transaction.archived.is_(False),
            Transaction.transfer_id.is_(None),
            Transaction.goal_id.is_(None),
        )
        .order_by(
            Transaction.transaction_date.desc(), Transaction.created_at.desc()
        )
    )
    rows = (await db.execute(stmt)).all()
    return [
        PeriodExpenseRow(
            amount=Decimal(str(amount)),
            currency=currency,
            category=category,
            txn_date=txn_date,
            envelope_name=envelope_name,
        )
        for amount, currency, category, txn_date, envelope_name in rows
    ]


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


# ── reclassify gasto ↔ ingreso (chat + app native) ───────────────────────────

RECLASSIFY_NOT_FOUND = "not_found"
RECLASSIFY_SHADOW = "shadow"
RECLASSIFY_TRANSFER_LEG = "transfer_leg"
RECLASSIFY_GOAL_FLOW = "goal_flow"
RECLASSIFY_ARCHIVED = "archived"
RECLASSIFY_ALREADY = "already_target"

# Voseo CR copy, shared by the chat reclassify surface. The native app flips a
# row directly via PATCH /transactions/{id}, so it doesn't read these.
RECLASSIFY_REASON_ES: dict[str, str] = {
    RECLASSIFY_NOT_FOUND: (
        "No tengo un movimiento reciente para reclasificar. "
        "Editalo desde Movimientos."
    ),
    RECLASSIFY_SHADOW: (
        "Ese movimiento está pendiente de aprobar. Aprobalo o rechazalo "
        "desde el bot."
    ),
    RECLASSIFY_TRANSFER_LEG: (
        "Ese movimiento es parte de una transferencia, no se puede "
        "reclasificar como gasto o ingreso."
    ),
    RECLASSIFY_GOAL_FLOW: (
        "Ese movimiento pertenece a una meta de ahorro. Gestionalo desde la "
        "meta."
    ),
    RECLASSIFY_ARCHIVED: "Restaurá el movimiento antes de reclasificarlo.",
    RECLASSIFY_ALREADY: "Ese movimiento ya está clasificado así.",
}


@dataclass
class ReclassifyError(Exception):
    """Raised when a gasto↔ingreso reclassification is not allowed.

    `reason_code` is machine-readable so the bot picks the Spanish copy from
    RECLASSIFY_REASON_ES without parsing human strings.
    """

    reason_code: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"reclassify_blocked:{self.reason_code}"


def reclassify_blocker(txn: Transaction, *, to_income: bool) -> Optional[str]:
    """Return a RECLASSIFY_* reason code if this row can't be flipped to the
    requested kind, else None. Pure — used both by the chat proposal builder
    (to refuse before staging) and by `reclassify_transaction` (to refuse at
    commit). Mirrors the PATCH immutability guards.
    """
    if txn.status != "confirmed":
        return RECLASSIFY_SHADOW
    if txn.transfer_id is not None:
        return RECLASSIFY_TRANSFER_LEG
    if txn.goal_id is not None:
        return RECLASSIFY_GOAL_FLOW
    if txn.archived:
        return RECLASSIFY_ARCHIVED
    if (txn.amount > 0) == to_income:
        return RECLASSIFY_ALREADY
    return None


async def reclassify_transaction(
    db: AsyncSession, *, txn: Transaction, to_income: bool
) -> Transaction:
    """Flip a confirmed movement between expense (amount < 0) and income
    (amount > 0) by changing only the SIGN of `amount` — magnitude is kept.

    Balances are computed live from `SUM(amount)`, so flipping the sign moves
    the row between the income and expense sides of the ledger with no stored
    balance to update. Shares the PATCH immutability guards. When the row
    becomes income, `envelope_id` is cleared — spending-cap sobres are
    expense-only. The category string is left untouched (it is informational
    and not kind-validated). FLUSHES, never commits; the caller owns the
    transaction boundary.
    """
    blocker = reclassify_blocker(txn, to_income=to_income)
    if blocker is not None:
        raise ReclassifyError(blocker)

    magnitude = abs(Decimal(str(txn.amount)))
    txn.amount = magnitude if to_income else -magnitude
    if to_income:
        # Income can never carry a spending-cap envelope.
        txn.envelope_id = None
    await db.flush()
    return txn


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
