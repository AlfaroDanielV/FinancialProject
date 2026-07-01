"""Loan cargo automático — a personal loan whose monthly cuota is auto-charged
to a linked credit card (CR "cargo automático").

When a ``Debt`` has ``charge_to_account_id`` set, its cuota is NOT paid from a
bank account; the bank charges it to the credit card and the user pays the card.
The daily worker (``workers/loan_cargo_daily.py`` → this module) posts the cuota
as a NEGATIVE ``Transaction`` on the card (``source='loan_cargo'`` — counts as a
card gasto, raises the live owed balance via ``compute_account_balances``) and
records a ``DebtPayment`` linking it (loan balance ↓, ``payments_made`` ++). The
loan steps aside from its own feed / affordability / reservation surfaces (its
servicing flows through the card) so nothing is double-counted.

Deterministic — no LLM. The bank usually does NOT email a cargo automático, so
the app is the system of record here; a rare duplicate (the bank *did* email it)
is resolved manually (delete the app charge → see ``undo_loan_cargo`` — or
discard the Gmail shadow).

Scheduling: the worker runs WEEKLY, so the posting logic is cadence-robust — it
anchors on the **most recent** cuota due date on/before today (this month's if its
due day has passed, else last month's) rather than the calendar month, and posts
that cuota if no cargo has been charged for it yet. This reliably posts every
monthly cuota including END-OF-MONTH due days (a calendar-month anchor would miss
them when the month rolls over before a weekly run). A bounded catch-up window
(``_MAX_CATCHUP_DAYS``) keeps it from backfilling a stale prior cycle (e.g. a
freshly-linked loan whose due day passed weeks ago) — it only ever posts the ONE
most-recent due, never a backlog. Idempotency is cargo-specific: a user's
manual/extra payment never suppresses the cargo (the bank still charges the card).
"""
from __future__ import annotations

import calendar
import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.account import Account
from ..models.debt import Debt, DebtPayment
from ..models.transaction import Transaction
from ..models.user import User
from .clock import user_today
from .debt_payments import record_debt_payment, restore_debt_balance

LOAN_CARGO_SOURCE = "loan_cargo"
_CARGO_CATEGORY = "Préstamo"
_CARGO_DESCRIPTION = "Cuota cargada a la tarjeta (cargo automático)"
_CARGO_NOTE = "Cargo automático a la tarjeta"
# How far back a passed due date may be and still be posted. Comfortably exceeds
# the WEEKLY cadence (even with one missed run ≈ 14 days) so no monthly cuota is
# lost, while staying under a month so a freshly-linked loan / a long run outage
# doesn't backfill a stale prior cycle.
_MAX_CATCHUP_DAYS = 20


def _clamp_due(year: int, month: int, due_day: int) -> date:
    """The cuota due date for (year, month), clamped to the month length."""
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(due_day, last_day))


def _most_recent_due(today: date, due_day: int) -> date:
    """The most recent cuota due date on or before ``today`` — this month's if its
    due day has already passed, otherwise last month's. Anchoring on this (not the
    calendar month) is what makes a WEEKLY schedule reliably catch end-of-month
    due days across the month rollover."""
    this_month = _clamp_due(today.year, today.month, due_day)
    if this_month <= today:
        return this_month
    if today.month == 1:
        return _clamp_due(today.year - 1, 12, due_day)
    return _clamp_due(today.year, today.month - 1, due_day)


async def post_due_loan_cargos(
    db: AsyncSession, *, user_id: uuid.UUID, today: Optional[date] = None
) -> list[DebtPayment]:
    """Post the most-recent-due cuota onto the linked card for every card-linked
    loan whose due day has passed (within the catch-up window) and that hasn't
    been CHARGED for that cuota yet.

    FLUSHES (via ``record_debt_payment``); the caller owns the commit so the
    card charge + the DebtPayment land atomically. Returns the DebtPayment rows
    created (one per loan charged).

    ``today`` defaults to the user's LOCAL date (``user_today`` — CR-first), so
    the due-day comparison honors the user's timezone, not the worker's UTC. Tests
    pass an explicit date for determinism.
    """
    debts = (
        await db.execute(
            select(Debt).where(
                Debt.user_id == user_id,
                Debt.is_active.is_(True),
                Debt.archived.is_(False),
                Debt.charge_to_account_id.is_not(None),
            )
        )
    ).scalars().all()
    if not debts:
        return []

    if today is None:
        user = await db.get(User, user_id)
        today = user_today(user) if user is not None else date.today()

    card_ids = {d.charge_to_account_id for d in debts}
    cards = {
        a.id: a
        for a in (
            await db.execute(
                select(Account).where(
                    Account.id.in_(card_ids),
                    Account.user_id == user_id,
                    Account.account_type == "credit",
                    Account.archived.is_(False),
                )
            )
        ).scalars().all()
    }

    posted: list[DebtPayment] = []

    for debt in debts:
        card = cards.get(debt.charge_to_account_id)
        if card is None:
            continue  # linked card archived / deleted / not a credit account
        # Same-currency v1 (the link validator enforces this; stay defensive).
        if (card.currency or "CRC") != (debt.currency or "CRC"):
            continue

        balance = Decimal(str(debt.current_balance))
        if balance <= 0:
            continue  # paid off — nothing to charge

        # The most recent cuota due on/before today. Weekly-safe: this catches an
        # end-of-month due day on the following week's run even after the month
        # rolled over (a calendar-month anchor would abandon it).
        due = _most_recent_due(today, debt.payment_due_day)
        # Bounded catch-up: don't backfill a stale prior cycle (a freshly-linked
        # loan whose due passed weeks ago, or a long run outage). Only ever the
        # single most-recent due — never a backlog.
        if (today - due).days > _MAX_CATCHUP_DAYS:
            continue

        # Idempotency (cargo-specific): already posted a CARGO for THIS cuota?
        # ">= due" matches this cycle's cargo but not last cycle's (whose date is
        # earlier), so the next cuota still posts. Scoped to loan_cargo-sourced
        # payments — a user's manual/extra payment is a DIFFERENT event and must
        # NOT suppress the cargo (the bank still charges the card).
        already = (
            await db.execute(
                select(DebtPayment.id)
                .join(Transaction, Transaction.id == DebtPayment.transaction_id)
                .where(
                    DebtPayment.debt_id == debt.id,
                    DebtPayment.payment_date >= due,
                    Transaction.source == LOAN_CARGO_SOURCE,
                )
                .limit(1)
            )
        ).first()
        if already:
            continue

        # Final cuota: never charge more than what's left.
        cuota = min(Decimal(str(debt.minimum_payment)), balance)
        if cuota <= 0:
            continue

        txn = Transaction(
            user_id=user_id,
            account_id=card.id,
            amount=-cuota,
            currency=debt.currency or "CRC",
            merchant=debt.name,
            description=_CARGO_DESCRIPTION,
            category=_CARGO_CATEGORY,
            transaction_date=due,
            source=LOAN_CARGO_SOURCE,
            status="confirmed",
        )
        db.add(txn)
        await db.flush()  # assign txn.id before linking the payment

        payment = await record_debt_payment(
            db,
            user_id=user_id,
            debt=debt,
            amount_paid=float(cuota),
            payment_date=due,
            notes=_CARGO_NOTE,
            transaction_id=txn.id,
        )
        posted.append(payment)

    return posted


async def undo_loan_cargo(
    db: AsyncSession, *, user_id: uuid.UUID, txn: Transaction
) -> None:
    """Reverse a posted cargo when the user deletes the card charge (the
    manual-dedup path). Restores the loan balance (+ decrements
    ``payments_made``), deletes the backing ``DebtPayment``, then deletes the
    charge. FLUSHES, never commits — the delete route owns the boundary.

    Required because the generic ``hard_delete_transaction`` blocks a
    debt-payment-linked row (it would orphan a paid marker); here we undo the
    payment first so the loan stays consistent.
    """
    payment = (
        await db.execute(
            select(DebtPayment)
            .join(Debt, Debt.id == DebtPayment.debt_id)
            .where(
                DebtPayment.transaction_id == txn.id,
                Debt.user_id == user_id,
            )
        )
    ).scalar_one_or_none()

    if payment is not None:
        debt = (
            await db.execute(
                select(Debt).where(Debt.id == payment.debt_id)
            )
        ).scalar_one()
        restore_debt_balance(debt, payment.amount_paid)
        await db.delete(payment)
        await db.flush()  # clear the FK before deleting the txn

    await db.delete(txn)
    await db.flush()
