"""Commit a PendingAction into the DB.

Action types: log_expense / log_income (transactions, via the shared
transactions service so the REST router and the bot produce the same rows)
and create_goal (Phase 6f conversational goal creation, mirroring
api/routers/goals.py::create_goal).
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.goal import Goal
from api.models.recurring_bill import RecurringBill
from api.models.recurring_income import RecurringIncome
from api.models.user import User
from api.services import recurrence
from api.services.transactions import create_transaction

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
