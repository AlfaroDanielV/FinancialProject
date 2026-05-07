"""Tests for api.services.gmail.whitelist.

Idempotency, soft-delete semantics, and the soft cap. Uses the
`db_with_user` fixture from conftest because the queries are real SQL —
mocking SQLAlchemy here would defeat the purpose.
"""
from __future__ import annotations

import socket
import uuid
from urllib.parse import urlparse

import pytest
from sqlalchemy import select

from api.config import settings
from api.models.gmail_sender_whitelist import GmailSenderWhitelist
from api.services.gmail import whitelist as wl


def _db_reachable() -> bool:
    try:
        url = urlparse(settings.database_url.replace("+asyncpg", ""))
        with socket.create_connection(
            (url.hostname or "localhost", url.port or 5432), timeout=0.5
        ):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="Postgres not reachable"
)


# ── add_sender ───────────────────────────────────────────────────────────────


async def test_add_sender_inserts(db_with_user):
    db, user_id = db_with_user
    row = await wl.add_sender(
        db=db,
        user_id=user_id,
        sender_email="notificaciones@bac.cr",
        bank_name="BAC",
        source=wl.SOURCE_PRESET,
    )
    await db.commit()
    assert row.sender_email == "notificaciones@bac.cr"
    assert row.bank_name == "BAC"
    assert row.source == wl.SOURCE_PRESET
    assert row.removed_at is None


async def test_add_sender_lowercases(db_with_user):
    db, user_id = db_with_user
    row = await wl.add_sender(
        db=db,
        user_id=user_id,
        sender_email="  Notificaciones@BAC.CR  ",
        bank_name="BAC",
        source=wl.SOURCE_PRESET,
    )
    await db.commit()
    assert row.sender_email == "notificaciones@bac.cr"


async def test_add_sender_idempotent_when_active(db_with_user):
    """Re-adding the same email while the row is active is a no-op."""
    db, user_id = db_with_user
    a = await wl.add_sender(
        db=db,
        user_id=user_id,
        sender_email="x@y.com",
        bank_name="Foo",
        source=wl.SOURCE_PRESET,
    )
    await db.commit()
    b = await wl.add_sender(
        db=db,
        user_id=user_id,
        sender_email="x@y.com",
        bank_name="OtherBank",  # should be ignored
        source=wl.SOURCE_CUSTOM,
    )
    await db.commit()
    assert a.id == b.id
    assert b.bank_name == "Foo"  # original wins
    rows = await wl.list_active(db=db, user_id=user_id)
    assert len(rows) == 1


async def test_add_sender_undeletes_previously_removed(db_with_user):
    """User adds, removes, re-adds the same email → upsert un-deletes
    the old row instead of creating a duplicate."""
    db, user_id = db_with_user
    row = await wl.add_sender(
        db=db,
        user_id=user_id,
        sender_email="x@y.com",
        bank_name="A",
        source=wl.SOURCE_PRESET,
    )
    await db.commit()
    original_id = row.id

    removed = await wl.remove_sender(
        db=db, user_id=user_id, sender_email="x@y.com"
    )
    assert removed is True
    await db.commit()

    re_add = await wl.add_sender(
        db=db,
        user_id=user_id,
        sender_email="x@y.com",
        bank_name="B",  # this time we DO take the new metadata
        source=wl.SOURCE_CUSTOM,
    )
    await db.commit()
    assert re_add.id == original_id
    assert re_add.removed_at is None
    assert re_add.bank_name == "B"
    assert re_add.source == wl.SOURCE_CUSTOM


# ── remove_sender ────────────────────────────────────────────────────────────


async def test_remove_sender_soft_deletes(db_with_user):
    db, user_id = db_with_user
    await wl.add_sender(
        db=db,
        user_id=user_id,
        sender_email="x@y.com",
        bank_name=None,
        source=wl.SOURCE_CUSTOM,
    )
    await db.commit()

    ok = await wl.remove_sender(db=db, user_id=user_id, sender_email="x@y.com")
    await db.commit()
    assert ok is True

    # Row still exists in the table — soft delete.
    rows = await db.execute(
        select(GmailSenderWhitelist).where(
            GmailSenderWhitelist.user_id == user_id
        )
    )
    row = rows.scalar_one()
    assert row.removed_at is not None

    # list_active filters it out.
    active = await wl.list_active(db=db, user_id=user_id)
    assert active == []


async def test_remove_sender_returns_false_when_absent(db_with_user):
    db, user_id = db_with_user
    ok = await wl.remove_sender(
        db=db, user_id=user_id, sender_email="nope@x.com"
    )
    assert ok is False


async def test_remove_sender_already_removed_returns_false(db_with_user):
    db, user_id = db_with_user
    await wl.add_sender(
        db=db,
        user_id=user_id,
        sender_email="x@y.com",
        bank_name=None,
        source=wl.SOURCE_CUSTOM,
    )
    await db.commit()
    await wl.remove_sender(db=db, user_id=user_id, sender_email="x@y.com")
    await db.commit()
    again = await wl.remove_sender(
        db=db, user_id=user_id, sender_email="x@y.com"
    )
    await db.commit()
    assert again is False


# ── list_active / count_active ──────────────────────────────────────────────


async def test_list_active_orders_by_added_at(db_with_user):
    db, user_id = db_with_user
    for s in ["c@x.com", "a@x.com", "b@x.com"]:
        await wl.add_sender(
            db=db,
            user_id=user_id,
            sender_email=s,
            bank_name=None,
            source=wl.SOURCE_CUSTOM,
        )
        await db.commit()
    rows = await wl.list_active(db=db, user_id=user_id)
    # Insertion order, not alphabetical.
    assert [r.sender_email for r in rows] == ["c@x.com", "a@x.com", "b@x.com"]


async def test_count_active(db_with_user):
    db, user_id = db_with_user
    assert await wl.count_active(db=db, user_id=user_id) == 0
    await wl.add_sender(
        db=db,
        user_id=user_id,
        sender_email="a@x.com",
        bank_name=None,
        source=wl.SOURCE_CUSTOM,
    )
    await db.commit()
    assert await wl.count_active(db=db, user_id=user_id) == 1
    await wl.remove_sender(db=db, user_id=user_id, sender_email="a@x.com")
    await db.commit()
    assert await wl.count_active(db=db, user_id=user_id) == 0
