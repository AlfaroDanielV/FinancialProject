"""Commit a PendingAction into the DB.

Action types: log_expense / log_income (transactions, via the shared
transactions service so the REST router and the bot produce the same rows),
log_transfer (Phase 7b, via the shared transfers service — same two legs the
REST endpoint creates) and create_goal (Phase 6f conversational goal
creation, mirroring api/routers/goals.py::create_goal).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.account import Account
from api.models.debt import Debt
from api.models.goal import Goal
from api.models.recurring_bill import RecurringBill
from api.models.recurring_income import RecurringIncome
from api.models.transaction import Transaction
from api.models.user import User
from api.schemas.transfers import TransferCreate
from api.services import recurrence
from api.services.anchors import apply_anchor
from api.services.transactions import (
    RECLASSIFY_NOT_FOUND,
    ReclassifyError,
    create_transaction,
    reclassify_transaction,
)
from api.services.transfers import create_transfer_with_transactions

from .pending import PendingAction, clear_pending, save_last_action
from .pending_db import resolve_from_pending


async def commit_pending(
    *,
    user: User,
    pending: PendingAction,
    db: AsyncSession,
    redis: Redis,
) -> uuid.UUID:
    """Commit the pending action, clear the pending key, stamp last_action.
    Returns the id of the newly created row (transaction or goal)."""

    if pending.action_type == "create_goal":
        return await _commit_goal(user=user, pending=pending, db=db, redis=redis)

    if pending.action_type == "create_income":
        return await _commit_income(user=user, pending=pending, db=db, redis=redis)

    if pending.action_type == "create_bill":
        return await _commit_bill(user=user, pending=pending, db=db, redis=redis)

    if pending.action_type == "attach_expense":
        return await _commit_attach_expense(
            user=user, pending=pending, db=db, redis=redis
        )

    if pending.action_type == "log_transfer":
        return await _commit_transfer(user=user, pending=pending, db=db, redis=redis)

    if pending.action_type == "reclassify":
        return await _commit_reclassify(
            user=user, pending=pending, db=db, redis=redis
        )

    if pending.action_type == "set_balance":
        return await _commit_set_balance(
            user=user, pending=pending, db=db, redis=redis
        )

    if pending.action_type not in ("log_expense", "log_income"):
        raise ValueError(f"unknown action_type: {pending.action_type}")

    payload = pending.payload
    amount = Decimal(payload["amount"])
    currency = payload["currency"]
    merchant = payload.get("merchant")
    category = payload.get("category")
    description = payload.get("description")
    txn_date = date.fromisoformat(payload["transaction_date"])
    account_raw: Optional[str] = payload.get("account_id")
    account_id = uuid.UUID(account_raw) if account_raw else None

    txn = await create_transaction(
        user=user,
        amount=amount,
        currency=currency,
        merchant=merchant,
        category=category,
        description=description,
        transaction_date=txn_date,
        account_id=account_id,
        source="telegram",
        db=db,
    )

    # Phase 5d: close the durable pending_confirmations row too.
    # create_transaction already committed; do the update + its own commit.
    await resolve_from_pending(
        session=db, pending=pending, resolution="confirmed"
    )
    await db.commit()

    await clear_pending(user_id=user.id, redis=redis)
    await save_last_action(
        user_id=user.id,
        action_type=pending.action_type,
        record_id=txn.id,
        redis=redis,
    )
    return txn.id


async def _commit_transfer(
    *,
    user: User,
    pending: PendingAction,
    db: AsyncSession,
    redis: Redis,
) -> uuid.UUID:
    """Create a transfer (two legs) from a confirmed log_transfer proposal —
    Phase 7b. Goes through the same create_transfer_with_transactions the REST
    endpoint uses so chat- and app-created transfers are identical rows (legs
    excluded from income/expense math via transfer_id). Returns the transfer id.
    """
    payload = pending.payload
    occurred_at = datetime.combine(
        date.fromisoformat(payload["occurred_at"]), time.min, tzinfo=timezone.utc
    )
    result = await create_transfer_with_transactions(
        db,
        user_id=user.id,
        payload=TransferCreate(
            from_account_id=uuid.UUID(payload["from_account_id"]),
            to_account_id=uuid.UUID(payload["to_account_id"]),
            amount=Decimal(payload["amount"]),
            currency=payload["currency"],
            occurred_at=occurred_at,
        ),
    )
    transfer_id = result.transfer.id
    await db.commit()

    await resolve_from_pending(
        session=db, pending=pending, resolution="confirmed"
    )
    await db.commit()

    await clear_pending(user_id=user.id, redis=redis)
    await save_last_action(
        user_id=user.id,
        action_type="log_transfer",
        record_id=transfer_id,
        redis=redis,
    )
    return transfer_id


async def _commit_reclassify(
    *,
    user: User,
    pending: PendingAction,
    db: AsyncSession,
    redis: Redis,
) -> uuid.UUID:
    """Flip the last movement gasto ↔ ingreso from a confirmed reclassify
    proposal. Re-fetches the row and re-checks the guards (via the shared
    service) — it may have changed between proposal and confirm. Updates
    last_action to the new kind so /undo and a re-reclassify still point at it.
    Returns the (unchanged) transaction id.
    """
    payload = pending.payload
    txn_id = uuid.UUID(payload["transaction_id"])
    to_income = bool(payload["to_income"])
    result = await db.execute(
        select(Transaction).where(
            Transaction.id == txn_id, Transaction.user_id == user.id
        )
    )
    txn = result.scalar_one_or_none()
    if txn is None:
        raise ReclassifyError(RECLASSIFY_NOT_FOUND)

    await reclassify_transaction(db, txn=txn, to_income=to_income)
    await db.commit()

    await resolve_from_pending(
        session=db, pending=pending, resolution="confirmed"
    )
    await db.commit()

    await clear_pending(user_id=user.id, redis=redis)
    await save_last_action(
        user_id=user.id,
        action_type="log_income" if to_income else "log_expense",
        record_id=txn_id,
        redis=redis,
    )
    return txn_id


async def _commit_set_balance(
    *,
    user: User,
    pending: PendingAction,
    db: AsyncSession,
    redis: Redis,
) -> uuid.UUID:
    """Re-anchor an account to its stated real balance (A7 chat "corregí mi
    saldo"). Appends an anchor + the informational ajuste via the shared
    `apply_anchor`, so the balance heals to the stated value in one step.

    Append-only and deliberately NOT added to the /undo chain (no
    save_last_action) — a wrong re-anchor is corrected by re-anchoring again,
    consistent with the append-only anchor model. Returns the new anchor id.
    """
    payload = pending.payload
    account_id = uuid.UUID(payload["account_id"])
    result = await db.execute(
        select(Account).where(
            Account.id == account_id, Account.user_id == user.id
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise ValueError("account not found for set_balance")

    res = await apply_anchor(
        db,
        user=user,
        account=account,
        value=Decimal(payload["value"]),
        source="reanchor",
        write_ajuste=True,
    )
    await db.commit()

    await resolve_from_pending(
        session=db, pending=pending, resolution="confirmed"
    )
    await db.commit()

    await clear_pending(user_id=user.id, redis=redis)
    return res.anchor_id


async def _commit_goal(
    *,
    user: User,
    pending: PendingAction,
    db: AsyncSession,
    redis: Redis,
) -> uuid.UUID:
    """Create a savings goal from a confirmed create_goal proposal. Mirrors
    api/routers/goals.py::create_goal so chat-created and SPA-created goals are
    identical rows. Returns the new goal id."""
    payload = pending.payload
    target_date = (
        date.fromisoformat(payload["target_date"])
        if payload.get("target_date")
        else None
    )
    goal = Goal(
        user_id=user.id,
        name=payload["name"],
        target_amount=Decimal(payload["target_amount"]),
        target_currency=payload["target_currency"],
        target_date=target_date,
    )
    db.add(goal)
    await db.commit()
    await db.refresh(goal)

    await resolve_from_pending(
        session=db, pending=pending, resolution="confirmed"
    )
    await db.commit()

    await clear_pending(user_id=user.id, redis=redis)
    await save_last_action(
        user_id=user.id,
        action_type="create_goal",
        record_id=goal.id,
        redis=redis,
    )
    return goal.id


async def _commit_income(
    *,
    user: User,
    pending: PendingAction,
    db: AsyncSession,
    redis: Redis,
) -> uuid.UUID:
    """Create a recurring income from a confirmed create_income proposal.
    Mirrors api/routers/recurring_incomes.py::create_recurring_income for the
    non-derived case (aguinaldo/salario_escolar are derived via the Incomes
    screen's one-tap action, never created here). Returns the new income id."""
    payload = pending.payload
    income = RecurringIncome(
        user_id=user.id,
        name=payload["name"],
        income_type=payload["income_type"],
        amount=Decimal(payload["amount"]),
        gross_monthly=(
            Decimal(payload["gross_monthly"])
            if payload.get("gross_monthly")
            else None
        ),
        currency=payload["currency"],
        frequency=payload["frequency"],
        next_payment_date=date.fromisoformat(payload["next_payment_date"]),
    )
    db.add(income)
    await db.commit()
    await db.refresh(income)

    await resolve_from_pending(
        session=db, pending=pending, resolution="confirmed"
    )
    await db.commit()

    await clear_pending(user_id=user.id, redis=redis)
    await save_last_action(
        user_id=user.id,
        action_type="create_income",
        record_id=income.id,
        redis=redis,
    )
    return income.id


async def _commit_attach_expense(
    *,
    user: User,
    pending: PendingAction,
    db: AsyncSession,
    redis: Redis,
) -> uuid.UUID:
    """Attach an existing recurring bill / debt to an envelope by setting its
    envelope_id (mirrors PATCH /recurring-bills|/debts with envelope_id). Returns
    the obligation id."""
    payload = pending.payload
    kind = payload["obligation_kind"]
    obligation_id = uuid.UUID(payload["obligation_id"])
    envelope_id = uuid.UUID(payload["envelope_id"])
    if kind == "card":
        # Phase 7b B5: the candidate id is the credit ACCOUNT id; the terms
        # row carries the envelope link (its minimum then reserves there).
        from api.models.credit_card_terms import CreditCardTerms

        row = (
            await db.execute(
                select(CreditCardTerms).where(
                    CreditCardTerms.account_id == obligation_id,
                    CreditCardTerms.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
    else:
        model = RecurringBill if kind == "bill" else Debt
        row = (
            await db.execute(
                select(model).where(
                    model.id == obligation_id, model.user_id == user.id
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise ValueError("obligation not found for attach_expense")
    row.envelope_id = envelope_id

    await resolve_from_pending(
        session=db, pending=pending, resolution="confirmed"
    )
    await db.commit()

    await clear_pending(user_id=user.id, redis=redis)
    await save_last_action(
        user_id=user.id,
        action_type="attach_expense",
        record_id=obligation_id,
        redis=redis,
    )
    return obligation_id


async def _commit_bill(
    *,
    user: User,
    pending: PendingAction,
    db: AsyncSession,
    redis: Redis,
) -> uuid.UUID:
    """Create a recurring bill (gasto fijo) + generate its occurrences, mirroring
    api/routers/recurring_bills.py::create_recurring_bill. Fixed-amount only
    (no is_variable_amount via chat yet). Returns the new bill id."""
    payload = pending.payload
    day_raw = payload.get("day_of_month")
    bill = RecurringBill(
        user_id=user.id,
        name=payload["name"],
        category=payload["category"],
        amount_expected=Decimal(payload["amount_expected"]),
        currency=payload["currency"],
        is_variable_amount=False,
        frequency=payload["frequency"],
        day_of_month=int(day_raw) if day_raw is not None else None,
        start_date=date.fromisoformat(payload["start_date"]),
        lead_time_days=0,
    )
    db.add(bill)
    await db.flush()
    await recurrence.generate_occurrences(bill, db)

    await resolve_from_pending(
        session=db, pending=pending, resolution="confirmed"
    )
    await db.commit()
    await db.refresh(bill)

    await clear_pending(user_id=user.id, redis=redis)
    await save_last_action(
        user_id=user.id,
        action_type="create_bill",
        record_id=bill.id,
        redis=redis,
    )
    return bill.id
