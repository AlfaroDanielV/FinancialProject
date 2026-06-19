"""Native Gmail surface (Phase 6 native) — sender whitelist + shadow review.

REST wrappers over `whitelist` + `shadow_review`, bearer-authed. The bot's
`/aprobar_shadow` / `/rechazar_shadow` share `shadow_review` (tested via the
service here).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.database import get_db
from api.main import app
from api.models.account import Account
from api.models.gmail_credential import GmailCredential
from api.models.gmail_ingestion_run import GmailIngestionRun
from api.models.gmail_message_seen import GmailMessageSeen
from api.models.gmail_sender_whitelist import GmailSenderWhitelist
from api.models.transaction import Transaction
from api.services.auth.magic_link import generate_link
from api.services.extraction.email_extractor import ExtractedEmailTransaction
from api.services.gmail.account_guess import (
    bank_name_for_sender,
    guess_account_id,
    load_guess_context,
)
from api.services.gmail.reconciler import ReconcileOutcome, reconcile


def _override_db(session):
    async def _yield():
        yield session

    app.dependency_overrides[get_db] = _yield


def _clear_db_override():
    app.dependency_overrides.pop(get_db, None)


async def _token(session, user_id) -> str:
    link = await generate_link(session, user_id=user_id, purpose="onboarding")
    _override_db(session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/auth/magic-link/exchange", json={"token": link.raw_token}
        )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _mk_shadow(session, user_id, gmail_id: str, *, amount=-1000.0, category="otros"):
    txn = Transaction(
        user_id=user_id,
        amount=amount,
        currency="CRC",
        merchant="BAC",
        category=category,
        transaction_date=date(2026, 6, 1),
        source="gmail",
        status="shadow",
        parse_status="confirmed",
        gmail_message_id=gmail_id,
    )
    session.add(txn)
    await session.flush()
    seen = GmailMessageSeen(
        user_id=user_id,
        gmail_message_id=gmail_id,
        outcome="created_shadow",
        transaction_id=txn.id,
    )
    session.add(seen)
    await session.commit()
    await session.refresh(txn)
    return txn


async def _mk_account(
    session, user_id, name, *, account_type="checking", currency="CRC"
):
    acc = Account(
        user_id=user_id,
        name=name,
        account_type=account_type,
        currency=currency,
        initial_balance=0,
        is_active=True,
        archived=False,
    )
    session.add(acc)
    await session.commit()
    await session.refresh(acc)
    return acc


# ── senders ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_senders_parses_dedupes_and_caps(db_with_user):
    session, user_id = db_with_user
    token = await _token(session, user_id)
    try:
        # 1 invalid, 1 dup, then 9 valid uniques → cap at 8.
        emails = "not-an-email, a@bac.cr, a@bac.cr, " + ", ".join(
            f"s{i}@bac.cr" for i in range(8)
        )
        async with _client() as ac:
            resp = await ac.post(
                "/api/v1/gmail/senders",
                json={"bank_name": "BAC", "emails": emails},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "not-an-email" in body["skipped"]
        # a@bac.cr + s0..s6 = 8 added (cap), s7 pushed over.
        assert len(body["added"]) == 8
        assert body["at_cap"] is True

        rows = (
            await session.execute(
                select(GmailSenderWhitelist).where(
                    GmailSenderWhitelist.user_id == user_id,
                    GmailSenderWhitelist.removed_at.is_(None),
                )
            )
        ).scalars().all()
        assert len(rows) == 8
        assert all(r.bank_name == "BAC" for r in rows)
    finally:
        _clear_db_override()


@pytest.mark.asyncio
async def test_list_and_remove_senders(db_with_user):
    session, user_id = db_with_user
    token = await _token(session, user_id)
    try:
        async with _client() as ac:
            add = await ac.post(
                "/api/v1/gmail/senders",
                json={"bank_name": "Promerica", "emails": "x@promerica.fi.cr"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert add.status_code == 200

            listed = await ac.get(
                "/api/v1/gmail/senders",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert listed.status_code == 200
            items = listed.json()
            assert len(items) == 1
            sender_id = items[0]["id"]
            assert items[0]["sender_email"] == "x@promerica.fi.cr"

            rm = await ac.delete(
                f"/api/v1/gmail/senders/{sender_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert rm.status_code == 200

            after = await ac.get(
                "/api/v1/gmail/senders",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert after.json() == []
    finally:
        _clear_db_override()


# ── shadow review ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_shadow_returns_only_gmail_shadow(db_with_user):
    session, user_id = db_with_user
    await _mk_shadow(session, user_id, "g1")
    # A confirmed gmail row + a manual shadow row must NOT appear.
    session.add(
        Transaction(
            user_id=user_id, amount=-50, currency="CRC", merchant="x",
            transaction_date=date(2026, 6, 1), source="gmail",
            status="confirmed", parse_status="confirmed", gmail_message_id="g2",
        )
    )
    await session.commit()
    token = await _token(session, user_id)
    try:
        async with _client() as ac:
            resp = await ac.get(
                "/api/v1/gmail/shadow",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200, resp.text
        items = resp.json()
        assert len(items) == 1
        assert items[0]["status"] == "shadow"
        assert items[0]["source"] == "gmail"
    finally:
        _clear_db_override()


@pytest.mark.asyncio
async def test_confirm_shadow_subset_applies_overrides(db_with_user):
    session, user_id = db_with_user
    t1 = await _mk_shadow(session, user_id, "g1", category="otros")
    t2 = await _mk_shadow(session, user_id, "g2", category="otros")
    token = await _token(session, user_id)
    try:
        async with _client() as ac:
            resp = await ac.post(
                "/api/v1/gmail/shadow/confirm",
                json={"items": [{"id": str(t1.id), "category": "alimentación"}]},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["confirmed"] == 1

        await session.refresh(t1)
        await session.refresh(t2)
        assert t1.status == "confirmed"
        assert t1.category == "alimentación"  # override applied
        assert t2.status == "shadow"  # untouched
    finally:
        _clear_db_override()


@pytest.mark.asyncio
async def test_confirm_shadow_links_category_id(db_with_user):
    """The native review picker sends category_id (FK) + name; confirm links
    the row to the user_categories row (the Categorías screen)."""
    session, user_id = db_with_user
    t1 = await _mk_shadow(session, user_id, "g1", category="otros")
    token = await _token(session, user_id)
    try:
        async with _client() as ac:
            # GET seeds the default categories; grab one's id.
            cats = await ac.get(
                "/api/v1/categories",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert cats.status_code == 200, cats.text
            alimentacion = next(c for c in cats.json() if c["name"] == "alimentación")

            resp = await ac.post(
                "/api/v1/gmail/shadow/confirm",
                json={
                    "items": [
                        {
                            "id": str(t1.id),
                            "category": alimentacion["name"],
                            "category_id": alimentacion["id"],
                        }
                    ]
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200, resp.text
        await session.refresh(t1)
        assert t1.status == "confirmed"
        assert t1.category == "alimentación"
        assert str(t1.category_id) == alimentacion["id"]
    finally:
        _clear_db_override()


@pytest.mark.asyncio
async def test_confirm_shadow_rejects_foreign_category_id(db_with_user):
    """A category_id that isn't the caller's active category is rejected 400
    (mirrors PATCH /transactions/{id})."""
    session, user_id = db_with_user
    t1 = await _mk_shadow(session, user_id, "g1", category="otros")
    token = await _token(session, user_id)
    try:
        async with _client() as ac:
            resp = await ac.post(
                "/api/v1/gmail/shadow/confirm",
                json={
                    "items": [
                        {"id": str(t1.id), "category_id": "00000000-0000-0000-0000-000000000000"}
                    ]
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 400, resp.text
        await session.refresh(t1)
        assert t1.status == "shadow"  # not confirmed
    finally:
        _clear_db_override()


@pytest.mark.asyncio
async def test_discard_shadow_marks_seen_and_deletes(db_with_user):
    session, user_id = db_with_user
    t1 = await _mk_shadow(session, user_id, "g1")
    t2 = await _mk_shadow(session, user_id, "g2")
    token = await _token(session, user_id)
    try:
        async with _client() as ac:
            resp = await ac.post(
                "/api/v1/gmail/shadow/discard",
                json={"ids": [str(t1.id)]},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["discarded"] == 1

        remaining = (
            await session.execute(
                select(Transaction.id).where(Transaction.user_id == user_id)
            )
        ).scalars().all()
        assert t1.id not in remaining
        assert t2.id in remaining

        seen = (
            await session.execute(
                select(GmailMessageSeen).where(
                    GmailMessageSeen.user_id == user_id,
                    GmailMessageSeen.gmail_message_id == "g1",
                )
            )
        ).scalar_one()
        assert seen.outcome == "rejected_by_user"
    finally:
        _clear_db_override()


@pytest.mark.asyncio
async def test_gmail_native_requires_auth(db_with_user):
    session, _user_id = db_with_user
    _override_db(session)
    try:
        async with _client() as ac:
            resp = await ac.get("/api/v1/gmail/shadow")
        assert resp.status_code == 401
    finally:
        _clear_db_override()


# ── scan guards + status feedback ──────────────────────────────────────────────


async def _connect(session, user_id):
    session.add(
        GmailCredential(
            user_id=user_id,
            kv_secret_name=f"gmail-refresh-{user_id}",
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            granted_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_scan_409_when_not_connected(db_with_user):
    session, user_id = db_with_user
    token = await _token(session, user_id)
    try:
        async with _client() as ac:
            resp = await ac.post(
                "/api/v1/gmail/scan", headers={"Authorization": f"Bearer {token}"}
            )
        assert resp.status_code == 409
    finally:
        _clear_db_override()


@pytest.mark.asyncio
async def test_scan_400_when_no_senders(db_with_user):
    session, user_id = db_with_user
    await _connect(session, user_id)
    token = await _token(session, user_id)
    try:
        async with _client() as ac:
            resp = await ac.post(
                "/api/v1/gmail/scan", headers={"Authorization": f"Bearer {token}"}
            )
        assert resp.status_code == 400
    finally:
        _clear_db_override()


@pytest.mark.asyncio
async def test_failed_message_is_retryable(db_with_user):
    """A transiently-`failed` message must be reprocessable, and a successful
    retry must promote it — but a later spurious failure must not clobber a
    terminal outcome."""
    from api.services.gmail.scanner import _already_seen, _mark_seen

    session, user_id = db_with_user
    await _mark_seen(
        db=session, user_id=user_id, message_id="m1", outcome="failed",
        transaction_id=None, ingestion_run_id=None, error={"reason": "revoked"},
    )
    await session.commit()
    assert await _already_seen(db=session, user_id=user_id, message_id="m1") is False

    await _mark_seen(
        db=session, user_id=user_id, message_id="m1", outcome="created_shadow",
        transaction_id=None, ingestion_run_id=None,
    )
    await session.commit()
    assert await _already_seen(db=session, user_id=user_id, message_id="m1") is True

    # A later spurious failed-mark must NOT overwrite the terminal outcome.
    await _mark_seen(
        db=session, user_id=user_id, message_id="m1", outcome="failed",
        transaction_id=None, ingestion_run_id=None,
    )
    await session.commit()
    row = (
        await session.execute(
            select(GmailMessageSeen).where(
                GmailMessageSeen.user_id == user_id,
                GmailMessageSeen.gmail_message_id == "m1",
            )
        )
    ).scalar_one()
    assert row.outcome == "created_shadow"


@pytest.mark.asyncio
async def test_scan_records_run_when_revoked(db_with_user):
    """Regression: a revoked/no-credential scan must still record a finished
    run (with an error), so the app's poll sees completion instead of hanging
    forever and can prompt reconnect."""
    from api.services.gmail.scanner import scan_user_inbox

    session, user_id = db_with_user  # no GmailCredential → revoked path
    result = await scan_user_inbox(
        user_id=user_id,
        since=datetime.now(timezone.utc) - timedelta(days=30),
        mode="manual",
        db=session,
    )
    assert result.revoked is True
    assert result.run_id is not None
    run = (
        await session.execute(
            select(GmailIngestionRun).where(GmailIngestionRun.user_id == user_id)
        )
    ).scalar_one()
    assert run.finished_at is not None  # finished, not stuck "running"
    assert run.errors is not None  # revoked recorded → has_errors


@pytest.mark.asyncio
async def test_scan_status_reports_state_and_latest_run(db_with_user):
    session, user_id = db_with_user
    await _connect(session, user_id)
    session.add(
        GmailIngestionRun(
            user_id=user_id,
            mode="manual",
            messages_scanned=4,
            transactions_created=2,
            transactions_matched=1,
            finished_at=datetime.now(timezone.utc),
        )
    )
    session.add(
        GmailSenderWhitelist(
            user_id=user_id, sender_email="a@bac.cr", bank_name="BAC", source="imported"
        )
    )
    await session.commit()
    token = await _token(session, user_id)
    try:
        async with _client() as ac:
            resp = await ac.get(
                "/api/v1/gmail/scan/status",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["connected"] is True
        assert body["revoked"] is False
        assert body["senders_count"] == 1
        assert body["latest_run"]["messages_scanned"] == 4
        assert body["latest_run"]["transactions_created"] == 2
        assert body["latest_run"]["running"] is False
        assert body["latest_run"]["has_errors"] is False
    finally:
        _clear_db_override()


# ── account guess (deterministic) ──────────────────────────────────────────────


def _acct(name, *, account_type="checking", currency="CRC", created=None, id_=None):
    """In-memory (unpersisted) Account for the pure guesser tests."""
    return Account(
        id=id_ or uuid.uuid4(),
        user_id=uuid.uuid4(),
        name=name,
        account_type=account_type,
        currency=currency,
        initial_balance=0,
        is_active=True,
        archived=False,
        created_at=created or datetime(2026, 1, 1),
    )


def test_guess_bank_name_match_beats_type_heuristic():
    bac = _acct("BAC", account_type="checking")
    scotia = _acct("Scotia Tarjeta", account_type="credit")
    # transaction_type=charge would prefer the credit card, but the bank name
    # pins it to the BAC checking account.
    got = guess_account_id(
        accounts=[bac, scotia], last_used={}, currency="CRC",
        transaction_type="charge", bank_name="BAC",
    )
    assert got == bac.id


def test_guess_currency_filter_blocks_other_currency():
    crc = _acct("Colones", currency="CRC")
    got = guess_account_id(
        accounts=[crc], last_used={}, currency="USD",
        transaction_type="charge", bank_name="BAC",
    )
    assert got is None  # no USD account → genuinely un-guessable


def test_guess_single_account_in_currency_fallback():
    crc = _acct("Colones", currency="CRC")
    usd = _acct("Dólares", currency="USD")
    got = guess_account_id(
        accounts=[crc, usd], last_used={}, currency="USD",
        transaction_type="withdrawal", bank_name=None,
    )
    assert got == usd.id


def test_guess_type_heuristic_prefers_credit_for_charge():
    checking = _acct("Corriente", account_type="checking")
    credit = _acct("Tarjeta", account_type="credit")
    got = guess_account_id(
        accounts=[checking, credit], last_used={}, currency="CRC",
        transaction_type="charge", bank_name=None,
    )
    assert got == credit.id


def test_guess_recency_tiebreak_among_same_type():
    a = _acct("Corriente A")
    b = _acct("Corriente B")
    got = guess_account_id(
        accounts=[a, b],
        last_used={a.id: date(2026, 3, 1), b.id: date(2026, 5, 1)},
        currency="CRC", transaction_type="withdrawal", bank_name=None,
    )
    assert got == b.id  # used more recently


def test_bank_name_for_sender_substring_match():
    m = {"notifica@bac.cr": "BAC", "alerts@bcr.fi.cr": "BCR"}
    assert bank_name_for_sender("BAC <notifica@bac.cr>", m) == "BAC"
    assert bank_name_for_sender("Nadie <x@y.com>", m) is None
    assert bank_name_for_sender(None, m) is None


# ── reconcile attaches the guess ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_attaches_guessed_account(db_with_user):
    session, user_id = db_with_user
    bac = await _mk_account(
        session, user_id, "BAC Visa", account_type="credit", currency="CRC"
    )
    candidate = ExtractedEmailTransaction(
        amount=Decimal("5000"), currency="CRC", merchant="Walmart",
        transaction_date=date(2026, 6, 1), transaction_type="charge",
        confidence=0.95,
    )
    accounts, last_used = await load_guess_context(session, user_id)
    outcome, txn = await reconcile(
        db=session, user_id=user_id, candidate=candidate,
        gmail_message_id="gm-guess-1", accounts=accounts, last_used=last_used,
        bank_name="BAC",
    )
    await session.commit()
    assert outcome == ReconcileOutcome.CREATED_SHADOW
    assert txn.account_id == bac.id


@pytest.mark.asyncio
async def test_reconcile_leaves_null_without_currency_account(db_with_user):
    session, user_id = db_with_user
    await _mk_account(session, user_id, "Colones", currency="CRC")
    candidate = ExtractedEmailTransaction(
        amount=Decimal("10"), currency="USD", merchant="Amazon",
        transaction_date=date(2026, 6, 1), transaction_type="charge",
        confidence=0.95,
    )
    accounts, last_used = await load_guess_context(session, user_id)
    outcome, txn = await reconcile(
        db=session, user_id=user_id, candidate=candidate,
        gmail_message_id="gm-guess-2", accounts=accounts, last_used=last_used,
        bank_name="BAC",
    )
    await session.commit()
    assert outcome == ReconcileOutcome.CREATED_SHADOW
    assert txn.account_id is None  # no USD account → stays "Sin cuenta"


# ── confirm carries account_id ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_confirm_shadow_applies_account_override(db_with_user):
    session, user_id = db_with_user
    t1 = await _mk_shadow(session, user_id, "g1")  # CRC
    acc = await _mk_account(session, user_id, "BAC", currency="CRC")
    token = await _token(session, user_id)
    try:
        async with _client() as ac:
            resp = await ac.post(
                "/api/v1/gmail/shadow/confirm",
                json={"items": [{"id": str(t1.id), "account_id": str(acc.id)}]},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200, resp.text
        await session.refresh(t1)
        assert t1.status == "confirmed"
        assert t1.account_id == acc.id
    finally:
        _clear_db_override()


@pytest.mark.asyncio
async def test_confirm_shadow_rejects_foreign_account(db_with_user):
    session, user_id = db_with_user
    t1 = await _mk_shadow(session, user_id, "g1")
    token = await _token(session, user_id)
    try:
        async with _client() as ac:
            resp = await ac.post(
                "/api/v1/gmail/shadow/confirm",
                json={
                    "items": [
                        {
                            "id": str(t1.id),
                            "account_id": "00000000-0000-0000-0000-000000000000",
                        }
                    ]
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 400, resp.text
        await session.refresh(t1)
        assert t1.status == "shadow"  # not confirmed
    finally:
        _clear_db_override()


@pytest.mark.asyncio
async def test_confirm_shadow_rejects_wrong_currency_account(db_with_user):
    session, user_id = db_with_user
    t1 = await _mk_shadow(session, user_id, "g1")  # CRC
    usd = await _mk_account(session, user_id, "Dólares", currency="USD")
    token = await _token(session, user_id)
    try:
        async with _client() as ac:
            resp = await ac.post(
                "/api/v1/gmail/shadow/confirm",
                json={"items": [{"id": str(t1.id), "account_id": str(usd.id)}]},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 400, resp.text
        await session.refresh(t1)
        assert t1.status == "shadow"
    finally:
        _clear_db_override()
