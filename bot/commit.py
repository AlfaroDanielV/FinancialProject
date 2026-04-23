"""Commit a PendingAction into the DB.

Only two action types today: log_expense, log_income. Both go through the
same transactions service so the REST router and the bot produce the same
rows.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.user import User
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
    """Create the transaction, clear the pending key, stamp last_action.
    Returns the newly created transaction id."""

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
