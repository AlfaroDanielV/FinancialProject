"""Native Gmail surface (Phase 6 native) — sender whitelist + shadow review.

REST wrappers over `whitelist` + `shadow_review`, bearer-authed. The bot's
`/aprobar_shadow` / `/rechazar_shadow` share `shadow_review` (tested via the
service here).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.database import get_db
from api.main import app
from api.models.gmail_credential import GmailCredential
from api.models.gmail_ingestion_run import GmailIngestionRun
from api.models.gmail_message_seen import GmailMessageSeen
from api.models.gmail_sender_whitelist import GmailSenderWhitelist
from api.models.transaction import Transaction
from api.services.auth.magic_link import generate_link


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
