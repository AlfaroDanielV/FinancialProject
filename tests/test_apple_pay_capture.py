"""Apple Pay zero-touch capture (iOS App Intent) — backend contracts.

Five regression fixtures from the plan:
  1. BAC contactless ₡ → Apple Pay event → later BAC email → exactly ONE
     transaction (the provisional row promoted in place), balance unchanged.
  2. USD-card contactless → stored in USD, no fake FX (no CRC conversion).
  3. Offline tap → retried with the same client_event_id → ONE transaction
     (idempotent), same id.
  4. Unparseable amount → 400, NO row written.
  5. ±5-day false-merge guard: two same-amount ₡ Apple Pay rows 4 days apart
     with dissimilar merchants → an ambiguous email does NOT silently merge the
     wrong one (it falls through to a shadow insert for the user to resolve).
Plus a positive disambiguation case (unique merchant-similar row DOES merge).

Endpoint tests use the ASGI client + dependency overrides (like
test_phase_6e_b5_transactions). Reconciliation tests call reconcile() directly
(like test_gmail_reconciler). Requires Postgres (the db_with_user fixture).
"""
from __future__ import annotations

import socket
import uuid
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from api.config import settings
from api.database import get_db
from api.dependencies import current_user
from api.main import app
from api.models.account import Account
from api.models.transaction import Transaction
from api.models.user import User
from api.services.accounts import compute_account_balances
from api.services.extraction.email_extractor import ExtractedEmailTransaction
from api.services.gmail.reconciler import ReconcileOutcome, reconcile


def _db_reachable() -> bool:
    try:
        url = urlparse(settings.database_url.replace("+asyncpg", ""))
        with socket.create_connection(
            (url.hostname or "localhost", url.port or 5432), timeout=0.5
        ):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable"),
    pytest.mark.asyncio,
]


# ── helpers ─────────────────────────────────────────────────────────────────


def _override(session, user):
    async def _yield_session():
        yield session

    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[get_db] = _yield_session


def _clear():
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(get_db, None)


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _account(db, user_id, *, name: str, currency: str = "CRC") -> Account:
    a = Account(
        user_id=user_id,
        name=name,
        account_type="credit",
        currency=currency,
        initial_balance=Decimal("0"),
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return a


async def _apple_pay_row(
    db, user_id, *, amount: Decimal, d: date, merchant: str, currency: str = "CRC"
) -> Transaction:
    """Insert a confirmed Apple Pay provisional row directly (the state the
    capture endpoint produces), for the reconciliation tests."""
    t = Transaction(
        user_id=user_id,
        amount=amount,
        currency=currency,
        merchant=merchant,
        transaction_date=d,
        source="apple_pay",
        status="confirmed",
        source_ref=f"apple_pay:{uuid.uuid4().hex}",
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


def _email(
    *, amount: Decimal, d: date, merchant: str, currency: str = "CRC"
) -> ExtractedEmailTransaction:
    return ExtractedEmailTransaction(
        transaction_type="charge",
        confidence=0.95,
        amount=amount,
        currency=currency,
        merchant=merchant,
        transaction_date=d,
    )


async def _count_txns(db, user_id) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(Transaction)
                .where(Transaction.user_id == user_id)
            )
        ).scalar_one()
    )


# ── Fixture 1 — BAC contactless ₡ merges to ONE row, balance unchanged ───────


async def test_apple_pay_then_bank_email_yields_one_transaction(db_with_user):
    db, user_id = db_with_user
    user = await db.get(User, user_id)
    acct = await _account(db, user_id, name="Tarjeta BAC")
    day = date(2026, 6, 20)

    _override(db, user)
    try:
        async with _client() as ac:
            resp = await ac.post(
                "/api/v1/transactions/apple-pay",
                json={
                    "amount": "5000",
                    "currency": "CRC",
                    "merchant": "POS TERMINAL 0042",
                    "client_event_id": "tap-bac-1",
                    "card_hint": "Tarjeta BAC",
                    "transaction_date": day.isoformat(),
                },
            )
    finally:
        _clear()

    assert resp.status_code == 201, resp.text
    provisional = resp.json()
    assert provisional["source"] == "apple_pay"
    assert provisional["status"] == "confirmed"
    assert provisional["account_id"] == str(acct.id)  # card_hint routed it
    assert Decimal(str(provisional["amount"])) == Decimal("-5000")

    bal_before = await compute_account_balances(
        db, user_id=user_id, account_ids=[acct.id]
    )
    assert bal_before[acct.id].current == Decimal("-5000")  # counts immediately

    # The bank email of the SAME purchase arrives 2 days later, different
    # merchant text (NFC terminal name ≠ bank-email merchant).
    outcome, merged = await reconcile(
        db=db,
        user_id=user_id,
        candidate=_email(
            amount=Decimal("5000"), d=day + timedelta(days=2), merchant="WALMART CR"
        ),
        gmail_message_id="msg-bac-1",
    )
    await db.commit()

    assert outcome == ReconcileOutcome.APPLE_PAY_MERGED
    assert merged.id == uuid.UUID(provisional["id"])  # same row, promoted
    assert merged.source == "reconciled"
    assert merged.gmail_message_id == "msg-bac-1"
    assert merged.status == "confirmed"

    assert await _count_txns(db, user_id) == 1  # never a second row
    bal_after = await compute_account_balances(
        db, user_id=user_id, account_ids=[acct.id]
    )
    assert bal_after[acct.id].current == Decimal("-5000")  # no double-count


# ── Fixture 2 — USD leg, no fake FX ──────────────────────────────────────────


async def test_usd_capture_stays_usd_no_fx(db_with_user):
    db, user_id = db_with_user
    user = await db.get(User, user_id)

    _override(db, user)
    try:
        async with _client() as ac:
            resp = await ac.post(
                "/api/v1/transactions/apple-pay",
                json={
                    "amount": "30.00",
                    "currency": "USD",
                    "merchant": "APPLE.COM/BILL",
                    "client_event_id": "tap-usd-1",
                    "transaction_date": "2026-06-20",
                },
            )
    finally:
        _clear()

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["currency"] == "USD"
    # Stored in the native currency — NOT converted to ₡15 000 at the ₡500 rate.
    assert Decimal(str(data["amount"])) == Decimal("-30")


# ── Fixture 3 — offline retry is idempotent ──────────────────────────────────


async def test_replay_same_client_event_id_is_noop(db_with_user):
    db, user_id = db_with_user
    user = await db.get(User, user_id)
    body = {
        "amount": "7500",
        "currency": "CRC",
        "merchant": "Soda La Esquina",
        "client_event_id": "tap-dup-xyz",
        "transaction_date": "2026-06-21",
    }

    _override(db, user)
    try:
        async with _client() as ac:
            first = await ac.post("/api/v1/transactions/apple-pay", json=body)
            second = await ac.post("/api/v1/transactions/apple-pay", json=body)
    finally:
        _clear()

    assert first.status_code == 201, first.text
    assert second.status_code in (200, 201), second.text
    assert first.json()["id"] == second.json()["id"]  # same row returned
    assert await _count_txns(db, user_id) == 1  # exactly one row


# ── Fixture 4 — unparseable amount → 400, no row ─────────────────────────────


@pytest.mark.parametrize("bad", ["abc", "0", "-5", ""])
async def test_unparseable_amount_rejected_no_row(db_with_user, bad):
    db, user_id = db_with_user
    user = await db.get(User, user_id)

    _override(db, user)
    try:
        async with _client() as ac:
            resp = await ac.post(
                "/api/v1/transactions/apple-pay",
                json={
                    "amount": bad,
                    "currency": "CRC",
                    "merchant": "X",
                    "client_event_id": f"tap-bad-{bad or 'empty'}",
                    "transaction_date": "2026-06-21",
                },
            )
    finally:
        _clear()

    # "" fails Pydantic min_length (422); the rest fail the parse (400).
    assert resp.status_code in (400, 422), resp.text
    assert await _count_txns(db, user_id) == 0  # nothing written


# ── Fixture 5 — ±5-day false-merge guard ─────────────────────────────────────


async def test_ambiguous_window_does_not_merge(db_with_user):
    db, user_id = db_with_user
    base = date(2026, 6, 18)
    # Two same-amount Apple Pay taps 4 days apart, dissimilar merchants.
    await _apple_pay_row(
        db, user_id, amount=Decimal("-5000"), d=base, merchant="Cafe Sol"
    )
    await _apple_pay_row(
        db,
        user_id,
        amount=Decimal("-5000"),
        d=base + timedelta(days=4),
        merchant="Ferreteria EPA",
    )

    # An email dated between them (within ±5d of BOTH) whose merchant matches
    # NEITHER → ambiguous → must NOT merge.
    outcome, _ = await reconcile(
        db=db,
        user_id=user_id,
        candidate=_email(
            amount=Decimal("5000"),
            d=base + timedelta(days=2),
            merchant="Pago en linea",
        ),
        gmail_message_id="msg-ambig-1",
    )
    await db.commit()

    assert outcome == ReconcileOutcome.CREATED_SHADOW  # fell through, no merge
    # 2 untouched apple_pay rows + 1 new shadow row.
    assert await _count_txns(db, user_id) == 3
    untouched = (
        await db.execute(
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.user_id == user_id,
                Transaction.source == "apple_pay",
                Transaction.gmail_message_id.is_(None),
            )
        )
    ).scalar_one()
    assert untouched == 2  # neither was wrongly promoted


async def test_unique_merchant_similar_merges_among_many(db_with_user):
    """The positive case: with two amount-window candidates, a UNIQUE
    merchant-similar one IS promoted (merchant breaks the tie)."""
    db, user_id = db_with_user
    base = date(2026, 6, 18)
    await _apple_pay_row(
        db, user_id, amount=Decimal("-5000"), d=base, merchant="Cafe Sol"
    )
    target = await _apple_pay_row(
        db,
        user_id,
        amount=Decimal("-5000"),
        d=base + timedelta(days=4),
        merchant="Walmart Escazu",
    )

    outcome, merged = await reconcile(
        db=db,
        user_id=user_id,
        candidate=_email(
            amount=Decimal("5000"),
            d=base + timedelta(days=2),
            merchant="WALMART CR",  # similar to "Walmart Escazu" only
        ),
        gmail_message_id="msg-uniq-1",
    )
    await db.commit()

    assert outcome == ReconcileOutcome.APPLE_PAY_MERGED
    assert merged.id == target.id
    assert merged.source == "reconciled"
    assert await _count_txns(db, user_id) == 2  # no third row
