"""CRUD-ish helpers for `gmail_sender_whitelist`.

Functions here are the only writers/readers of the table outside
migrations. Every entry/exit point lowercases `sender_email` so we
don't end up with two rows for `User@bac.cr` and `user@bac.cr`.

Soft delete by convention: `remove_sender` flips `removed_at`, never
DELETEs. The Gmail scanner filters `WHERE removed_at IS NULL`. A user
who removes-and-re-adds the same email triggers an upsert that nullifies
`removed_at` instead of inserting a new row — see `add_sender` below.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.gmail_sender_whitelist import GmailSenderWhitelist


# Allowed `source` values mirror the CHECK in migration 0012.
SOURCE_PRESET = "preset_tap"
SOURCE_CUSTOM = "custom_typed"
SOURCE_IMPORTED = "imported"

ACTIVE_CAP = 8


def normalize_email(s: str) -> str:
    """Lowercase + strip. The CHECK on `source` is the only DB-side
    validation; email shape is the caller's job (handlers regex-validate
    before calling)."""
    return s.strip().lower()


async def add_sender(
    *,
    db: AsyncSession,
    user_id: uuid.UUID,
    sender_email: str,
    bank_name: Optional[str] = None,
    source: str = SOURCE_CUSTOM,
) -> GmailSenderWhitelist:
    """Idempotent upsert.

    - If no row exists for (user_id, sender_email): INSERT.
    - If a row exists with `removed_at IS NOT NULL` (previously removed):
      un-delete it. Update bank_name and source to the latest values.
    - If a row exists with `removed_at IS NULL` (already active): no-op,
      return the existing row. We don't refresh bank_name/source because
      the original signal — typed in by the user or chosen via preset —
      is more authoritative than a re-add.

    Returns the row in its current state.
    """
    email = normalize_email(sender_email)

    # First check whether an active row exists; cheap and lets us skip
    # the upsert dance for the no-op case.
    existing = await db.execute(
        select(GmailSenderWhitelist).where(
            GmailSenderWhitelist.user_id == user_id,
            GmailSenderWhitelist.sender_email == email,
        )
    )
    row = existing.scalar_one_or_none()

    if row is not None and row.removed_at is None:
        return row

    if row is not None and row.removed_at is not None:
        # Soft-undelete path.
        row.removed_at = None
        row.bank_name = bank_name
        row.source = source
        row.added_at = datetime.now(timezone.utc)
        await db.flush()
        return row

    # Fresh insert.
    row = GmailSenderWhitelist(
        user_id=user_id,
        sender_email=email,
        bank_name=bank_name,
        source=source,
    )
    db.add(row)
    await db.flush()
    return row


async def remove_sender(
    *, db: AsyncSession, user_id: uuid.UUID, sender_email: str
) -> bool:
    """Soft delete. Returns True if a row was flipped, False if nothing
    matched (already removed, never existed)."""
    email = normalize_email(sender_email)
    result = await db.execute(
        update(GmailSenderWhitelist)
        .where(GmailSenderWhitelist.user_id == user_id)
        .where(GmailSenderWhitelist.sender_email == email)
        .where(GmailSenderWhitelist.removed_at.is_(None))
        .values(removed_at=datetime.now(timezone.utc))
        .returning(GmailSenderWhitelist.id)
    )
    return result.scalar_one_or_none() is not None


async def remove_sender_by_id(
    *, db: AsyncSession, user_id: uuid.UUID, sender_id: uuid.UUID
) -> bool:
    """Soft delete by id (used by /quitar_banco inline button)."""
    result = await db.execute(
        update(GmailSenderWhitelist)
        .where(GmailSenderWhitelist.id == sender_id)
        .where(GmailSenderWhitelist.user_id == user_id)
        .where(GmailSenderWhitelist.removed_at.is_(None))
        .values(removed_at=datetime.now(timezone.utc))
        .returning(GmailSenderWhitelist.id)
    )
    return result.scalar_one_or_none() is not None


async def list_active(
    *, db: AsyncSession, user_id: uuid.UUID
) -> list[GmailSenderWhitelist]:
    """Active senders for a user, sorted by added_at ascending — the
    order the user added them, which feels right when displayed."""
    rows = await db.execute(
        select(GmailSenderWhitelist)
        .where(GmailSenderWhitelist.user_id == user_id)
        .where(GmailSenderWhitelist.removed_at.is_(None))
        .order_by(GmailSenderWhitelist.added_at.asc())
    )
    return list(rows.scalars().all())


async def count_active(*, db: AsyncSession, user_id: uuid.UUID) -> int:
    """Used to enforce the soft cap (ACTIVE_CAP) before adding more."""
    rows = await db.execute(
        select(GmailSenderWhitelist.id)
        .where(GmailSenderWhitelist.user_id == user_id)
        .where(GmailSenderWhitelist.removed_at.is_(None))
    )
    return len(list(rows.scalars().all()))
