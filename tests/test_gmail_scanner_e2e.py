"""End-to-end scanner tests with httpx MockTransport.

We mock:
  - Gmail API at the HTTP layer (MockTransport).
  - The OAuth refresh token retrieval (monkeypatched on
    api.services.gmail.scanner._resolve_access_token).
  - The LLM client via FixtureLLMClient.

We exercise the full pipeline: list → get → extract → reconcile → mark
seen. Requires Postgres for reconciler / DB state.
"""
from __future__ import annotations

import base64
import json
import socket
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
import pytest
from sqlalchemy import select

from api.config import settings
from api.models.gmail_credential import GmailCredential
from api.models.gmail_ingestion_run import GmailIngestionRun
from api.models.gmail_message_seen import GmailMessageSeen
from api.models.gmail_sender_whitelist import GmailSenderWhitelist
from api.models.transaction import Transaction
from api.services.extraction.email_extractor import (
    EmailExtractionError,
)
from api.services.gmail import scanner as scanner_mod
from api.services.gmail.reconciler import ReconcileOutcome
from api.services.llm_extractor.client import (
    FixtureLLMClient,
    RecordedLLMResponse,
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


# ── helpers to fabricate Gmail API JSON payloads ─────────────────────────────


def _b64url(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode().rstrip("=")


def _msg_payload(msg_id: str, body_text: str, *, sender: str, subject: str):
    """Mimic the shape of users.messages.get(format='full')."""
    return {
        "id": msg_id,
        "threadId": "t-" + msg_id,
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
            ],
            "body": {"data": _b64url(body_text)},
        },
    }


def _list_payload(message_ids: list[str], next_page: str | None = None):
    body = {"messages": [{"id": i, "threadId": "t-" + i} for i in message_ids]}
    if next_page:
        body["nextPageToken"] = next_page
    return body


def _make_gmail_mock(routes: dict):
    """`routes` maps `(path_suffix, query_dict_subset)` to JSON dicts.
    Simpler version: maps full path → list of responses (popped in order).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/gmail/v1/users/me/messages":
            page_token = request.url.params.get("pageToken")
            key = f"list:{page_token or ''}"
            if key in routes:
                return httpx.Response(200, json=routes[key])
            return httpx.Response(404, json={"error": "no list route"})
        if path.startswith("/gmail/v1/users/me/messages/"):
            mid = path.rsplit("/", 1)[-1]
            key = f"get:{mid}"
            if key in routes:
                resp = routes[key]
                if isinstance(resp, tuple):  # (status, json) for errors
                    return httpx.Response(resp[0], json=resp[1])
                return httpx.Response(200, json=resp)
            return httpx.Response(404, json={"error": f"no get route for {mid}"})
        return httpx.Response(404, json={"error": f"unknown path {path}"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def stub_token(monkeypatch):
    """Pretend the user has a valid refresh token; skip the real KV+OAuth
    refresh path."""

    async def fake_resolve(*, user_id, db):
        return "fake-access-token"

    monkeypatch.setattr(scanner_mod, "_resolve_access_token", fake_resolve)


# ── helpers to seed user / whitelist ────────────────────────────────────────


async def _setup_active_user(db, user_id, *, days_ago=0, senders=None):
    cred = GmailCredential(
        user_id=user_id,
        kv_secret_name=f"gmail-refresh-{user_id}",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        granted_at=datetime.now(timezone.utc) - timedelta(days=days_ago + 1),
        activated_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )
    db.add(cred)
    for s in senders or ["notificaciones@bac.cr"]:
        db.add(
            GmailSenderWhitelist(
                user_id=user_id,
                sender_email=s,
                bank_name="BAC",
                source="preset_tap",
            )
        )
    await db.commit()


# ── happy path: 2 messages, both new ────────────────────────────────────────


async def test_scan_creates_shadow_rows_when_in_window(
    db_with_user, stub_token
):
    db, user_id = db_with_user
    await _setup_active_user(db, user_id)  # in shadow

    msg1 = _msg_payload(
        "m1",
        body_text="Compra por ₡5,000 en Walmart con tarjeta ****1234",
        sender="notificaciones@bac.cr",
        subject="Notificación de compra",
    )
    msg2 = _msg_payload(
        "m2",
        body_text="Compra por ₡8,500 en Uber con tarjeta ****1234",
        sender="notificaciones@bac.cr",
        subject="Notificación de compra",
    )
    routes = {
        "list:": _list_payload(["m1", "m2"]),
        "get:m1": msg1,
        "get:m2": msg2,
    }
    http = _make_gmail_mock(routes)

    fixture = FixtureLLMClient(
        by_message={
            "Compra por ₡5,000 en Walmart con tarjeta ****1234": RecordedLLMResponse(
                tool_input={
                    "transaction_type": "charge",
                    "confidence": 0.95,
                    "amount": "5000",
                    "currency": "CRC",
                    "merchant": "Walmart",
                    "transaction_date": str(datetime.now(timezone.utc).date()),
                    "last4": "1234",
                }
            ),
            "Compra por ₡8,500 en Uber con tarjeta ****1234": RecordedLLMResponse(
                tool_input={
                    "transaction_type": "charge",
                    "confidence": 0.95,
                    "amount": "8500",
                    "currency": "CRC",
                    "merchant": "Uber",
                    "transaction_date": str(datetime.now(timezone.utc).date()),
                    "last4": "1234",
                }
            ),
        }
    )

    result = await scanner_mod.scan_user_inbox(
        user_id=user_id,
        since=datetime.now(timezone.utc) - timedelta(days=30),
        until=None,
        mode="backfill",
        db=db,
        llm_client=fixture,
        http=http,
    )
    await http.aclose()

    assert result.messages_scanned == 2
    assert result.transactions_created == 2
    assert result.transactions_matched == 0
    assert not result.revoked

    # Both transactions inserted with status='shadow'.
    rows = await db.execute(
        select(Transaction).where(Transaction.user_id == user_id)
    )
    txns = list(rows.scalars().all())
    assert len(txns) == 2
    assert all(t.status == "shadow" for t in txns)
    assert all(t.source == "gmail" for t in txns)

    # gmail_messages_seen has 2 rows with outcome='created_shadow'.
    seen = await db.execute(
        select(GmailMessageSeen).where(GmailMessageSeen.user_id == user_id)
    )
    seen_rows = list(seen.scalars().all())
    assert len(seen_rows) == 2
    assert {s.outcome for s in seen_rows} == {"created_shadow"}

    # gmail_ingestion_runs row finished with the right counters.
    run_row = (
        await db.execute(
            select(GmailIngestionRun).where(
                GmailIngestionRun.user_id == user_id
            )
        )
    ).scalar_one()
    assert run_row.messages_scanned == 2
    assert run_row.transactions_created == 2
    assert run_row.finished_at is not None


# ── dedup: a message already in gmail_messages_seen is skipped ──────────────


async def test_scan_skips_already_seen_messages(db_with_user, stub_token):
    db, user_id = db_with_user
    await _setup_active_user(db, user_id)

    # Pre-seed a "seen" record for m1.
    db.add(
        GmailMessageSeen(
            user_id=user_id,
            gmail_message_id="m1",
            outcome="created_shadow",
        )
    )
    await db.commit()

    msg2 = _msg_payload(
        "m2",
        body_text="Cargo por ₡3,000",
        sender="notificaciones@bac.cr",
        subject="x",
    )
    routes = {
        "list:": _list_payload(["m1", "m2"]),
        # No get route for m1 — if scanner calls it, the test fails (404).
        "get:m2": msg2,
    }
    http = _make_gmail_mock(routes)

    fixture = FixtureLLMClient(
        default=RecordedLLMResponse(
            tool_input={
                "transaction_type": "charge",
                "confidence": 0.95,
                "amount": "3000",
                "currency": "CRC",
                "transaction_date": str(datetime.now(timezone.utc).date()),
            }
        )
    )
    result = await scanner_mod.scan_user_inbox(
        user_id=user_id,
        since=datetime.now(timezone.utc) - timedelta(days=30),
        until=None,
        mode="manual",
        db=db,
        llm_client=fixture,
        http=http,
    )
    await http.aclose()

    # m1 should not have been fetched (would have 404'd) and not contributed.
    # messages_scanned counts list iterations regardless of dedup; what matters
    # is that the only NEW transaction is for m2.
    assert result.transactions_created == 1


# ── empty whitelist: aborts cleanly ──────────────────────────────────────────


async def test_scan_aborts_when_whitelist_empty(db_with_user, stub_token):
    db, user_id = db_with_user
    cred = GmailCredential(
        user_id=user_id,
        kv_secret_name=f"gmail-refresh-{user_id}",
        scopes=[],
        granted_at=datetime.now(timezone.utc),
        activated_at=datetime.now(timezone.utc),
    )
    db.add(cred)
    await db.commit()

    # No senders.
    result = await scanner_mod.scan_user_inbox(
        user_id=user_id,
        since=datetime.now(timezone.utc) - timedelta(days=30),
        until=None,
        mode="manual",
        db=db,
    )
    assert result.no_whitelist
    assert result.messages_scanned == 0


# ── revoked credential: marks revoked_at, returns ──────────────────────────


async def test_scan_marks_revoked_when_no_token(
    db_with_user, monkeypatch
):
    db, user_id = db_with_user
    await _setup_active_user(db, user_id)

    async def fake_resolve(*, user_id, db):
        return None  # signals "credential gone / invalid_grant"

    monkeypatch.setattr(scanner_mod, "_resolve_access_token", fake_resolve)

    result = await scanner_mod.scan_user_inbox(
        user_id=user_id,
        since=datetime.now(timezone.utc) - timedelta(days=30),
        until=None,
        mode="daily",
        db=db,
    )
    assert result.revoked
    assert result.messages_scanned == 0


# ── matched_existing: pre-existing telegram row gets reconciled ─────────────


async def test_scan_reconciles_pre_existing_transaction(
    db_with_user, stub_token
):
    from datetime import date as _date
    from decimal import Decimal

    db, user_id = db_with_user
    await _setup_active_user(db, user_id, days_ago=10)  # OUT of shadow

    today = _date.today()
    pre = Transaction(
        user_id=user_id,
        amount=Decimal("-7500"),
        currency="CRC",
        transaction_date=today,
        source="telegram",
    )
    db.add(pre)
    await db.commit()
    await db.refresh(pre)

    msg = _msg_payload(
        "m1",
        body_text="Cargo por ₡7,500 en Walmart",
        sender="notificaciones@bac.cr",
        subject="x",
    )
    routes = {"list:": _list_payload(["m1"]), "get:m1": msg}
    http = _make_gmail_mock(routes)

    fixture = FixtureLLMClient(
        default=RecordedLLMResponse(
            tool_input={
                "transaction_type": "charge",
                "confidence": 0.95,
                "amount": "7500",
                "currency": "CRC",
                "merchant": "Walmart",
                "transaction_date": str(today),
            }
        )
    )
    result = await scanner_mod.scan_user_inbox(
        user_id=user_id,
        since=datetime.now(timezone.utc) - timedelta(days=30),
        until=None,
        mode="manual",
        db=db,
        llm_client=fixture,
        http=http,
    )
    await http.aclose()

    assert result.transactions_matched == 1
    assert result.transactions_created == 0

    refreshed = (
        await db.execute(select(Transaction).where(Transaction.id == pre.id))
    ).scalar_one()
    assert refreshed.gmail_message_id == "m1"
    assert refreshed.source == "reconciled"
