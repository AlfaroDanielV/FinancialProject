"""DB-backed tests for the reconciler.

We exercise the 5 ReconcileOutcome paths plus the sign-application logic.
Uses the `db_with_user` fixture from conftest, which requires Postgres.
"""
from __future__ import annotations

import socket
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlparse

import pytest
from sqlalchemy import select

from api.config import settings
from api.models.gmail_credential import GmailCredential
from api.models.transaction import Transaction
from api.services.extraction.email_extractor import (
    ExtractedEmailTransaction,
)
from api.services.gmail.reconciler import (
    AMOUNT_TOLERANCE,
    LOOKBACK_DAYS,
    MATCH_WINDOW_DAYS,
    SHADOW_WINDOW_DAYS,
    ReconcileOutcome,
    _signed_amount,
    reconcile,
)


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


# ── _signed_amount (pure) ───────────────────────────────────────────────────
# These don't actually need DB but they're related and run with the rest.


def test_signed_amount_charge_negates():
    c = ExtractedEmailTransaction(
        transaction_type="charge", confidence=0.9, amount=Decimal("5000")
    )
    assert _signed_amount(c) == Decimal("-5000")


def test_signed_amount_deposit_keeps_positive():
    c = ExtractedEmailTransaction(
        transaction_type="deposit", confidence=0.9, amount=Decimal("100000")
    )
    assert _signed_amount(c) == Decimal("100000")


def test_signed_amount_unknown_returns_none():
    c = ExtractedEmailTransaction(
        transaction_type="unknown", confidence=0.9, amount=Decimal("5000")
    )
    assert _signed_amount(c) is None


# ── helpers ─────────────────────────────────────────────────────────────────


async def _activate_with_window(
    db, user_id, *, days_ago: int = 0
) -> GmailCredential:
    """Insert a gmail_credentials row; activated_at controls shadow window."""
    cred = GmailCredential(
        user_id=user_id,
        kv_secret_name=f"gmail-refresh-{user_id}",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        granted_at=datetime.now(timezone.utc) - timedelta(days=days_ago + 1),
        activated_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )
    db.add(cred)
    await db.commit()
    return cred


async def _seed_transaction(
    db,
    user_id,
    *,
    amount: Decimal,
    txn_date: date,
    currency: str = "CRC",
    source: str = "telegram",
    merchant: str = "Manual entry",
) -> Transaction:
    t = Transaction(
        user_id=user_id,
        amount=amount,
        currency=currency,
        merchant=merchant,
        transaction_date=txn_date,
        source=source,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


# ── outcome 1: SKIPPED_LOW_CONFIDENCE ────────────────────────────────────────


async def test_skipped_when_confidence_below_threshold(db_with_user):
    db, user_id = db_with_user
    await _activate_with_window(db, user_id, days_ago=0)

    candidate = ExtractedEmailTransaction(
        transaction_type="charge",
        confidence=0.5,
        amount=Decimal("1000"),
        currency="CRC",
        transaction_date=date.today(),
    )
    outcome, txn = await reconcile(
        db=db,
        user_id=user_id,
        candidate=candidate,
        gmail_message_id="msg-1",
    )
    assert outcome == ReconcileOutcome.SKIPPED_LOW_CONFIDENCE
    assert txn is None
    # Nothing was inserted.
    rows = await db.execute(
        select(Transaction).where(Transaction.user_id == user_id)
    )
    assert rows.scalar_one_or_none() is None


async def test_skipped_when_amount_missing(db_with_user):
    db, user_id = db_with_user
    await _activate_with_window(db, user_id, days_ago=0)

    candidate = ExtractedEmailTransaction(
        transaction_type="charge",
        confidence=0.95,
        amount=None,
        currency="CRC",
        transaction_date=date.today(),
    )
    outcome, txn = await reconcile(
        db=db, user_id=user_id, candidate=candidate, gmail_message_id="msg-1"
    )
    assert outcome == ReconcileOutcome.SKIPPED_LOW_CONFIDENCE
    assert txn is None


async def test_skipped_when_type_unknown(db_with_user):
    db, user_id = db_with_user
    await _activate_with_window(db, user_id, days_ago=0)

    candidate = ExtractedEmailTransaction(
        transaction_type="unknown",
        confidence=0.95,
        amount=Decimal("5000"),
        currency="CRC",
        transaction_date=date.today(),
    )
    outcome, _ = await reconcile(
        db=db, user_id=user_id, candidate=candidate, gmail_message_id="msg-1"
    )
    assert outcome == ReconcileOutcome.SKIPPED_LOW_CONFIDENCE


# ── outcome 2: DUPLICATE_GMAIL ──────────────────────────────────────────────


async def test_duplicate_when_same_gmail_id_already_ingested(db_with_user):
    db, user_id = db_with_user
    await _activate_with_window(db, user_id, days_ago=0)

    # Pre-existing gmail-sourced row.
    existing = Transaction(
        user_id=user_id,
        amount=Decimal("-3000"),
        currency="CRC",
        transaction_date=date.today(),
        source="gmail",
        gmail_message_id="msg-X",
    )
    db.add(existing)
    await db.commit()

    candidate = ExtractedEmailTransaction(
        transaction_type="charge",
        confidence=0.95,
        amount=Decimal("3000"),
        currency="CRC",
        transaction_date=date.today(),
    )
    outcome, returned = await reconcile(
        db=db,
        user_id=user_id,
        candidate=candidate,
        gmail_message_id="msg-X",
    )
    assert outcome == ReconcileOutcome.DUPLICATE_GMAIL
    assert returned is not None
    assert returned.id == existing.id


# ── outcome 3: MATCHED_EXISTING ─────────────────────────────────────────────


async def test_matches_pre_existing_transaction_within_window(db_with_user):
    """User logged the charge via /gasté in Telegram; the email confirms
    it. We should mark the row reconciled, not insert a duplicate."""
    db, user_id = db_with_user
    await _activate_with_window(db, user_id, days_ago=0)

    today = date.today()
    # Seed with merchant=None so the reconciler's "backfill if missing"
    # branch is exercised. If the user had typed a merchant manually we
    # would NOT overwrite it.
    pre = Transaction(
        user_id=user_id,
        amount=Decimal("-5000"),
        currency="CRC",
        transaction_date=today,
        source="telegram",
    )
    db.add(pre)
    await db.commit()
    await db.refresh(pre)

    candidate = ExtractedEmailTransaction(
        transaction_type="charge",
        confidence=0.95,
        amount=Decimal("5000"),
        currency="CRC",
        transaction_date=today,
        merchant="Walmart",
    )
    outcome, txn = await reconcile(
        db=db,
        user_id=user_id,
        candidate=candidate,
        gmail_message_id="msg-2",
    )
    assert outcome == ReconcileOutcome.MATCHED_EXISTING
    assert txn is not None
    assert txn.id == pre.id
    await db.commit()
    refreshed = (
        await db.execute(select(Transaction).where(Transaction.id == pre.id))
    ).scalar_one()
    assert refreshed.gmail_message_id == "msg-2"
    assert refreshed.source == "reconciled"
    assert refreshed.merchant == "Walmart"  # backfilled from candidate


async def test_match_does_not_overwrite_existing_merchant(db_with_user):
    """When the user already typed a merchant, gmail email shouldn't
    overwrite it — the user's input is more authoritative."""
    db, user_id = db_with_user
    await _activate_with_window(db, user_id, days_ago=0)

    today = date.today()
    pre = await _seed_transaction(
        db,
        user_id,
        amount=Decimal("-5000"),
        txn_date=today,
        currency="CRC",
        source="telegram",
        merchant="user-typed name",
    )

    candidate = ExtractedEmailTransaction(
        transaction_type="charge",
        confidence=0.95,
        amount=Decimal("5000"),
        currency="CRC",
        transaction_date=today,
        merchant="DIFFERENT NAME FROM EMAIL",
    )
    outcome, _ = await reconcile(
        db=db, user_id=user_id, candidate=candidate, gmail_message_id="msg-2b"
    )
    assert outcome == ReconcileOutcome.MATCHED_EXISTING
    refreshed = (
        await db.execute(select(Transaction).where(Transaction.id == pre.id))
    ).scalar_one()
    assert refreshed.merchant == "user-typed name"


async def test_match_tolerates_amount_off_by_one(db_with_user):
    """The CRC amount written by the user (5000) matches the email's
    5001 within AMOUNT_TOLERANCE."""
    db, user_id = db_with_user
    await _activate_with_window(db, user_id, days_ago=0)

    today = date.today()
    pre = await _seed_transaction(
        db, user_id, amount=Decimal("-5000"), txn_date=today
    )

    candidate = ExtractedEmailTransaction(
        transaction_type="charge",
        confidence=0.95,
        amount=Decimal("5001"),
        currency="CRC",
        transaction_date=today,
    )
    outcome, _ = await reconcile(
        db=db, user_id=user_id, candidate=candidate, gmail_message_id="msg-3"
    )
    assert outcome == ReconcileOutcome.MATCHED_EXISTING


async def test_no_match_when_amount_differs_beyond_tolerance(db_with_user):
    db, user_id = db_with_user
    await _activate_with_window(db, user_id, days_ago=10)  # OUT of shadow

    today = date.today()
    await _seed_transaction(
        db, user_id, amount=Decimal("-5000"), txn_date=today
    )

    candidate = ExtractedEmailTransaction(
        transaction_type="charge",
        confidence=0.95,
        amount=Decimal("5050"),
        currency="CRC",
        transaction_date=today,
    )
    outcome, _ = await reconcile(
        db=db, user_id=user_id, candidate=candidate, gmail_message_id="msg-4"
    )
    # Shouldn't match → should CREATE_NEW (post-shadow).
    assert outcome == ReconcileOutcome.CREATED_NEW


async def test_no_match_when_currency_differs(db_with_user):
    db, user_id = db_with_user
    await _activate_with_window(db, user_id, days_ago=10)

    today = date.today()
    await _seed_transaction(
        db, user_id, amount=Decimal("-5000"), txn_date=today, currency="USD"
    )
    candidate = ExtractedEmailTransaction(
        transaction_type="charge",
        confidence=0.95,
        amount=Decimal("5000"),
        currency="CRC",
        transaction_date=today,
    )
    outcome, _ = await reconcile(
        db=db, user_id=user_id, candidate=candidate, gmail_message_id="msg-5"
    )
    assert outcome == ReconcileOutcome.CREATED_NEW


async def test_match_picks_closest_amount_when_two_candidates(db_with_user):
    """Two transactions could match by date+currency; the one with the
    smallest amount diff wins."""
    db, user_id = db_with_user
    await _activate_with_window(db, user_id, days_ago=0)

    today = date.today()
    far = await _seed_transaction(
        db, user_id, amount=Decimal("-5001"), txn_date=today, merchant="far"
    )
    close = await _seed_transaction(
        db, user_id, amount=Decimal("-5000"), txn_date=today, merchant="close"
    )

    candidate = ExtractedEmailTransaction(
        transaction_type="charge",
        confidence=0.95,
        amount=Decimal("5000"),
        currency="CRC",
        transaction_date=today,
    )
    outcome, txn = await reconcile(
        db=db, user_id=user_id, candidate=candidate, gmail_message_id="msg-6"
    )
    assert outcome == ReconcileOutcome.MATCHED_EXISTING
    assert txn is not None
    assert txn.id == close.id


# ── outcomes 4 & 5: CREATED_SHADOW / CREATED_NEW ────────────────────────────


async def test_created_shadow_when_in_window(db_with_user):
    db, user_id = db_with_user
    await _activate_with_window(db, user_id, days_ago=0)  # IN shadow

    candidate = ExtractedEmailTransaction(
        transaction_type="charge",
        confidence=0.95,
        amount=Decimal("7000"),
        currency="CRC",
        transaction_date=date.today(),
    )
    outcome, txn = await reconcile(
        db=db, user_id=user_id, candidate=candidate, gmail_message_id="msg-7"
    )
    assert outcome == ReconcileOutcome.CREATED_SHADOW
    assert txn is not None
    assert txn.status == "shadow"
    assert txn.source == "gmail"
    assert txn.gmail_message_id == "msg-7"
    assert txn.amount == Decimal("-7000")


async def test_created_confirmed_when_outside_window(db_with_user):
    db, user_id = db_with_user
    await _activate_with_window(db, user_id, days_ago=10)  # OUT of shadow

    candidate = ExtractedEmailTransaction(
        transaction_type="deposit",
        confidence=0.95,
        amount=Decimal("100000"),
        currency="CRC",
        transaction_date=date.today(),
    )
    outcome, txn = await reconcile(
        db=db, user_id=user_id, candidate=candidate, gmail_message_id="msg-8"
    )
    assert outcome == ReconcileOutcome.CREATED_NEW
    assert txn is not None
    assert txn.status == "confirmed"
    assert txn.amount == Decimal("100000")  # deposit → positive


async def test_no_activated_at_treated_as_shadow(db_with_user):
    """A user who never tapped Activar (activated_at=NULL) — defensive
    branch: treat as shadow so we don't pollute the balance."""
    db, user_id = db_with_user
    cred = GmailCredential(
        user_id=user_id,
        kv_secret_name=f"gmail-refresh-{user_id}",
        scopes=[],
        granted_at=datetime.now(timezone.utc),
        activated_at=None,
    )
    db.add(cred)
    await db.commit()

    candidate = ExtractedEmailTransaction(
        transaction_type="charge",
        confidence=0.95,
        amount=Decimal("3000"),
        currency="CRC",
        transaction_date=date.today(),
    )
    outcome, txn = await reconcile(
        db=db, user_id=user_id, candidate=candidate, gmail_message_id="msg-9"
    )
    assert outcome == ReconcileOutcome.CREATED_SHADOW
    assert txn is not None
    assert txn.status == "shadow"


# ── window edges ────────────────────────────────────────────────────────────


async def test_match_within_date_window():
    """MATCH_WINDOW_DAYS = 1 means same-day ±1 day are matchable."""
    assert MATCH_WINDOW_DAYS == 1


async def test_shadow_window_is_seven_days():
    assert SHADOW_WINDOW_DAYS == 7


async def test_amount_tolerance_constant_is_one():
    assert AMOUNT_TOLERANCE == Decimal("1")
