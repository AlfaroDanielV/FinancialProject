"""Shared shadow-transaction review logic for Gmail ingestion.

Gmail-parsed transactions land as `status='shadow', source='gmail'` during the
7-day shadow window (see `reconciler.py`). The user reviews them and either
confirms (→ `status='confirmed'`) or discards (mark the seen-row
`rejected_by_user` + delete the transaction).

This module is the single source of truth for that logic — both the Telegram
bot (`/aprobar_shadow`, `/rechazar_shadow`) and the native REST endpoints
(`/api/v1/gmail/shadow/*`) call it, so approve/reject behaves identically on
both surfaces. Deterministic; no LLM.

`PATCH /transactions/{id}` deliberately 409s on shadow rows, so per-row edits
from the native review screen are applied here, atomically, at confirm time.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.gmail_message_seen import GmailMessageSeen
from ...models.transaction import Transaction


@dataclass(frozen=True)
class ShadowEdit:
    """Optional per-row overrides applied at confirm time. Any field left
    None keeps the parsed value. `amount` carries its own sign (the native
    edit modal preserves expense/income sign)."""

    id: uuid.UUID
    amount: Optional[float] = None
    merchant: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    category_id: Optional[uuid.UUID] = None
    transaction_date: Optional[date] = None


def _shadow_where(stmt, user_id: uuid.UUID):
    return (
        stmt.where(Transaction.user_id == user_id)
        .where(Transaction.status == "shadow")
        .where(Transaction.source == "gmail")
    )


async def list_shadow(db: AsyncSession, *, user_id: uuid.UUID) -> list[Transaction]:
    """All pending Gmail shadow rows for a user, newest first."""
    rows = await db.execute(
        _shadow_where(select(Transaction), user_id)
        .order_by(Transaction.transaction_date.desc(), Transaction.created_at.desc())
    )
    return list(rows.scalars().all())


async def confirm_shadow(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    items: Optional[list[ShadowEdit]] = None,
) -> int:
    """Promote shadow rows to `confirmed`, applying optional per-row edits.

    `items=None` confirms ALL of the user's Gmail shadow rows (the bot's
    "approve all" path). Otherwise only the listed ids are confirmed, with
    their overrides applied. Returns the number confirmed. Scoped to
    shadow+gmail+user, so a stray id can never confirm someone else's row or
    a non-shadow row.
    """
    stmt = _shadow_where(select(Transaction), user_id)
    edits: dict[uuid.UUID, ShadowEdit] = {}
    if items is not None:
        if not items:
            return 0
        edits = {it.id: it for it in items}
        stmt = stmt.where(Transaction.id.in_(list(edits.keys())))

    rows = list((await db.execute(stmt)).scalars().all())
    for row in rows:
        edit = edits.get(row.id)
        if edit is not None:
            if edit.amount is not None:
                row.amount = edit.amount
            if edit.merchant is not None:
                row.merchant = edit.merchant
            if edit.description is not None:
                row.description = edit.description
            if edit.category is not None:
                row.category = edit.category
            if edit.category_id is not None:
                row.category_id = edit.category_id
            if edit.transaction_date is not None:
                row.transaction_date = edit.transaction_date
        row.status = "confirmed"

    await db.commit()
    return len(rows)


async def discard_shadow(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    ids: Optional[list[uuid.UUID]] = None,
) -> int:
    """Reject shadow rows: mark their `gmail_messages_seen` row
    `rejected_by_user` (audit trail) then delete the transaction.

    `ids=None` discards ALL of the user's Gmail shadow rows (the bot's
    "reject all" path). Returns the number discarded.
    """
    sel = _shadow_where(
        select(Transaction.id, Transaction.gmail_message_id), user_id
    )
    if ids is not None:
        if not ids:
            return 0
        sel = sel.where(Transaction.id.in_(ids))

    targets = (await db.execute(sel)).all()
    if not targets:
        return 0

    txn_ids = [t.id for t in targets]
    gmail_ids = [t.gmail_message_id for t in targets if t.gmail_message_id]

    # Mark seen-rows rejected BEFORE deleting the transactions
    # (gmail_messages_seen.transaction_id is ON DELETE SET NULL).
    if gmail_ids:
        await db.execute(
            update(GmailMessageSeen)
            .where(GmailMessageSeen.user_id == user_id)
            .where(GmailMessageSeen.gmail_message_id.in_(gmail_ids))
            .values(outcome="rejected_by_user")
        )
    await db.execute(delete(Transaction).where(Transaction.id.in_(txn_ids)))
    await db.commit()
    return len(txn_ids)
