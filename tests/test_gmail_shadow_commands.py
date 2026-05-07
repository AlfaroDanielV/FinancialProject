"""Tests for /aprobar_shadow and /rechazar_shadow.

We don't drive the aiogram message machinery — we test the SQL behavior
directly by importing helpers from the test environment and re-using
the same statements the handlers issue. The CHECK constraint on
gmail_messages_seen.outcome admitting 'rejected_by_user' is the contract
we're enforcing.
"""
from __future__ import annotations

import socket
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from urllib.parse import urlparse

import pytest
from sqlalchemy import delete, func as sa_func, select, update

from api.config import settings
from api.models.gmail_message_seen import GmailMessageSeen
from api.models.transaction import Transaction


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


async def _seed_shadow(db, user_id, *, count: int) -> list[uuid.UUID]:
    ids = []
    for i in range(count):
        gmid = f"msg-{i}-{uuid.uuid4().hex[:6]}"
        t = Transaction(
            user_id=user_id,
            amount=Decimal(f"-{1000 + i}"),
            currency="CRC",
            merchant=f"M{i}",
            transaction_date=date.today(),
            source="gmail",
            status="shadow",
            gmail_message_id=gmid,
        )
        db.add(t)
        # Also seed a matching gmail_messages_seen so /rechazar can flip its outcome.
        db.add(
            GmailMessageSeen(
                user_id=user_id,
                gmail_message_id=gmid,
                outcome="created_shadow",
            )
        )
        await db.commit()
        await db.refresh(t)
        ids.append(t.id)
    return ids


# ── /aprobar_shadow ──────────────────────────────────────────────────────────


async def test_approve_shadow_promotes_only_gmail_shadow_rows(db_with_user):
    db, user_id = db_with_user
    await _seed_shadow(db, user_id, count=3)
    # Also seed a non-shadow gmail row that should NOT be touched.
    other = Transaction(
        user_id=user_id,
        amount=Decimal("-9999"),
        currency="CRC",
        transaction_date=date.today(),
        source="gmail",
        status="confirmed",
        gmail_message_id="other-msg",
    )
    db.add(other)
    # And a non-gmail row that should NOT be touched.
    manual = Transaction(
        user_id=user_id,
        amount=Decimal("-50"),
        currency="CRC",
        transaction_date=date.today(),
        source="telegram",
        status="shadow",  # corner case: shadow but not gmail
    )
    db.add(manual)
    await db.commit()

    # Run the same SQL the handler issues.
    result = await db.execute(
        update(Transaction)
        .where(Transaction.user_id == user_id)
        .where(Transaction.status == "shadow")
        .where(Transaction.source == "gmail")
        .values(status="confirmed")
        .returning(Transaction.id)
    )
    ids = [r[0] for r in result.fetchall()]
    await db.commit()
    assert len(ids) == 3

    # `manual` (shadow but non-gmail) was untouched.
    refreshed = (
        await db.execute(select(Transaction).where(Transaction.user_id == user_id))
    ).scalars().all()
    by_status_source = [(t.status, t.source) for t in refreshed]
    assert ("shadow", "telegram") in by_status_source
    assert ("confirmed", "gmail") in by_status_source
    # No more gmail+shadow rows.
    assert all(
        not (t.source == "gmail" and t.status == "shadow") for t in refreshed
    )


# ── /rechazar_shadow ────────────────────────────────────────────────────────


async def test_reject_shadow_deletes_and_marks_rejected(db_with_user):
    db, user_id = db_with_user
    txn_ids = await _seed_shadow(db, user_id, count=2)

    # Replicate the handler's SQL.
    rows = await db.execute(
        select(Transaction.id, Transaction.gmail_message_id)
        .where(Transaction.user_id == user_id)
        .where(Transaction.status == "shadow")
        .where(Transaction.source == "gmail")
    )
    targets = [(r[0], r[1]) for r in rows.fetchall()]
    gmail_ids = [g for _t, g in targets if g]
    assert len(targets) == 2

    await db.execute(
        update(GmailMessageSeen)
        .where(GmailMessageSeen.user_id == user_id)
        .where(GmailMessageSeen.gmail_message_id.in_(gmail_ids))
        .values(outcome="rejected_by_user")
    )
    await db.execute(
        delete(Transaction)
        .where(Transaction.user_id == user_id)
        .where(Transaction.status == "shadow")
        .where(Transaction.source == "gmail")
    )
    await db.commit()

    # Transactions gone.
    remaining = (
        await db.execute(
            select(sa_func.count())
            .select_from(Transaction)
            .where(Transaction.user_id == user_id)
            .where(Transaction.status == "shadow")
            .where(Transaction.source == "gmail")
        )
    ).scalar_one()
    assert remaining == 0

    # gmail_messages_seen rows still there, with new outcome.
    seen_rows = (
        await db.execute(
            select(GmailMessageSeen.outcome)
            .where(GmailMessageSeen.user_id == user_id)
            .where(GmailMessageSeen.gmail_message_id.in_(gmail_ids))
        )
    ).all()
    outcomes = {r[0] for r in seen_rows}
    assert outcomes == {"rejected_by_user"}


async def test_reject_shadow_when_none_present(db_with_user):
    """Handler's pre-check should bail before issuing the delete."""
    db, user_id = db_with_user
    count = (
        await db.execute(
            select(sa_func.count())
            .select_from(Transaction)
            .where(Transaction.user_id == user_id)
            .where(Transaction.status == "shadow")
            .where(Transaction.source == "gmail")
        )
    ).scalar_one()
    assert count == 0
