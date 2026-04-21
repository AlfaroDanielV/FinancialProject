"""Undo flow for the Telegram bot.

Spec: hard-delete the last committed action with three guards:
  1. Row must belong to the user (enforced in the service).
  2. Row.source must be 'telegram'.
  3. No bill_occurrence.transaction_id may reference it.

The Redis last_action key has a 24h TTL; the user's /undo intent wins as
long as the key + the row are both still there.
"""
from __future__ import annotations

import uuid
from typing import Tuple

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.user import User
from api.services.transactions import (
    UNDO_REASON_LINKED_TO_BILL,
    UNDO_REASON_NOT_FOUND,
    UNDO_REASON_WRONG_SOURCE,
    UndoGuardError,
    delete_telegram_transaction,
)

from . import messages_es
from .pending import clear_last_action, load_last_action


async def run_undo(
    *, user: User, db: AsyncSession, redis: Redis
) -> Tuple[bool, str]:
    """Returns (ok, spanish_reply). `ok=True` on success."""
    entry = await load_last_action(user_id=user.id, redis=redis)
    if not entry:
        return False, messages_es.UNDO_NOTHING

    action_type = entry.get("action_type", "")
    raw_id = entry.get("record_id", "")
    try:
        record_id = uuid.UUID(raw_id)
    except (ValueError, TypeError):
        await clear_last_action(user_id=user.id, redis=redis)
        return False, messages_es.UNDO_NOT_FOUND

    if action_type not in ("log_expense", "log_income"):
        await clear_last_action(user_id=user.id, redis=redis)
        return False, messages_es.UNDO_NOT_FOUND

    try:
        await delete_telegram_transaction(
            user=user, transaction_id=record_id, db=db
        )
    except UndoGuardError as e:
        if e.reason_code == UNDO_REASON_NOT_FOUND:
            await clear_last_action(user_id=user.id, redis=redis)
            return False, messages_es.UNDO_NOT_FOUND
        if e.reason_code == UNDO_REASON_WRONG_SOURCE:
            return False, messages_es.UNDO_WRONG_SOURCE
        if e.reason_code == UNDO_REASON_LINKED_TO_BILL:
            return False, messages_es.UNDO_LINKED
        return False, messages_es.UNDO_NOT_FOUND

    await clear_last_action(user_id=user.id, redis=redis)
    return True, messages_es.UNDO_SUCCESS
