from __future__ import annotations

import json
import socket
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from api.config import settings
from api.models.gmail_discovery_run import GmailDiscoveryRun
from api.services.gmail.discovery import (
    build_discovery_query,
    discover_senders,
    normalize_keywords,
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


def test_normalize_keywords_dedupes_and_caps_at_five():
    out = normalize_keywords(
        [" transacción ", "compra", "Compra", "", "movimiento", "x", "y", "z"]
    )
    assert out == ["transacción", "compra", "movimiento", "x", "y"]


def test_build_discovery_query_for_keywords_and_sender_test():
    q = build_discovery_query(["transacción", "se realizó"], days=30)
    assert "(in:inbox OR in:spam)" in q
    assert "subject:(" in q
    assert '"se realizó"' in q
    assert "newer_than:30d" in q

    sender_q = build_discovery_query(["notificaciones@bac.cr"], days=7)
    assert sender_q == "from:notificaciones@bac.cr newer_than:7d"


def _transport(messages: dict[str, tuple[str, str]]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            body = {"messages": [{"id": mid} for mid in messages.keys()]}
            return httpx.Response(200, json=body)

        msg_id = request.url.path.rsplit("/", 1)[-1]
        from_value, subject = messages[msg_id]
        payload = {
            "payload": {
                "headers": [
                    {"name": "From", "value": from_value},
                    {"name": "Subject", "value": subject},
                ]
            }
        }
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


async def test_discover_senders_groups_by_sender_and_persists(db_with_user):
    db, user_id = db_with_user
    messages = {
        "1": ("BAC <notificacion@notificacionesbaccr.com>", "Compra realizada"),
        "2": ("notificacion@notificacionesbaccr.com", "Transacción aprobada"),
        "3": ("Uber <no-reply@uber.com>", "Tu viaje"),
        "4": ("notificaciones@promerica.fi.cr", "Movimiento"),
    }
    async with httpx.AsyncClient(transport=_transport(messages)) as http:
        result = await discover_senders(
            user_id,
            ["transacción"],
            db=db,
            http=http,
            access_token="token",
            enforce_cooldown=False,
        )

    assert result.total_messages_scanned == 4
    assert [s.email for s in result.senders] == [
        "notificacion@notificacionesbaccr.com",
        "no-reply@uber.com",
        "notificaciones@promerica.fi.cr",
    ]
    assert result.senders[0].count == 2
    assert result.senders[0].sample_subjects[:2] == [
        "Compra realizada",
        "Transacción aprobada",
    ]

    # Scope to this test's user — the table may hold rows from other tests /
    # prior runs, so an unfiltered .one() is fragile (MultipleResultsFound).
    row = (
        await db.execute(
            select(GmailDiscoveryRun).where(GmailDiscoveryRun.user_id == user_id)
        )
    ).scalars().one()
    assert row.keywords == ["transacción"]
    assert row.senders_found[0]["email"] == "notificacion@notificacionesbaccr.com"


async def test_discover_senders_empty_result(db_with_user):
    db, user_id = db_with_user

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"messages": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await discover_senders(
            user_id,
            ["comprobante"],
            db=db,
            http=http,
            access_token="token",
            enforce_cooldown=False,
        )

    assert result.senders == []
    assert result.total_messages_scanned == 0


async def test_discover_senders_skips_malformed_from(db_with_user, caplog):
    db, user_id = db_with_user
    messages = {
        "1": ("not an email", "Movimiento"),
        "2": ("Banco <alerts@bcr.fi.cr>", "Movimiento"),
    }
    async with httpx.AsyncClient(transport=_transport(messages)) as http:
        result = await discover_senders(
            user_id,
            ["movimiento"],
            db=db,
            http=http,
            access_token="token",
            enforce_cooldown=False,
        )

    assert [s.email for s in result.senders] == ["alerts@bcr.fi.cr"]
    assert "gmail_discovery_bad_from" in caplog.text

