"""Gmail scan-window clamp — less-confusing onboarding.

`run_backfill` clamps `since` UP to the Gmail connection time (`activated_at`)
so a fresh setup doesn't dredge up a pile of pre-connection emails (whose effect
on the anchored balance is non-obvious). The N-day window is the MAX lookback,
not a floor; once the connection is older than N days the full window applies.
"""
from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from urllib.parse import urlparse

import pytest

from api.config import settings
from api.models.gmail_credential import GmailCredential
from api.services.gmail import backfill as backfill_mod
from api.services.gmail.scanner import ScanResult


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


class _SessionCtx:
    """Yields the test session for run_backfill's `async with
    AsyncSessionLocal()` blocks (so its own-session reads see test data)."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *a):
        return None


async def _cred(db, user_id, *, activated_days_ago: int):
    db.add(
        GmailCredential(
            user_id=user_id,
            kv_secret_name=f"gmail-refresh-{user_id}",
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            granted_at=datetime.now(timezone.utc)
            - timedelta(days=activated_days_ago + 1),
            activated_at=datetime.now(timezone.utc)
            - timedelta(days=activated_days_ago),
        )
    )
    await db.commit()


def _patch(monkeypatch, session, captured):
    monkeypatch.setattr(
        backfill_mod, "AsyncSessionLocal", lambda: _SessionCtx(session)
    )
    monkeypatch.setattr(
        backfill_mod.notifier, "notify_run_started", AsyncMock()
    )
    monkeypatch.setattr(
        backfill_mod.notifier, "notify_run_completed", AsyncMock()
    )

    async def fake_scan(**kwargs):
        captured["since"] = kwargs["since"]
        return ScanResult(
            user_id=kwargs["user_id"],
            mode=kwargs.get("mode", "backfill"),
            started_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(backfill_mod, "scan_user_inbox", fake_scan)


async def test_backfill_clamps_window_to_connection(db_with_user, monkeypatch):
    """Connected yesterday + 30-day window → scan from ~connection, not 30 days
    (no pre-connection pile)."""
    session, user_id = db_with_user
    await _cred(session, user_id, activated_days_ago=1)
    captured: dict = {}
    _patch(monkeypatch, session, captured)

    await backfill_mod.run_backfill(user_id=user_id, days=30)

    age_days = (datetime.now(timezone.utc) - captured["since"]).days
    assert age_days <= 1  # clamped to the connection time, not 30 days back


async def test_backfill_full_window_for_old_connection(db_with_user, monkeypatch):
    """Connected long ago → the full N-day window applies (clamp is a no-op)."""
    session, user_id = db_with_user
    await _cred(session, user_id, activated_days_ago=90)
    captured: dict = {}
    _patch(monkeypatch, session, captured)

    await backfill_mod.run_backfill(user_id=user_id, days=30)

    age_days = (datetime.now(timezone.utc) - captured["since"]).days
    assert age_days >= 29  # full 30-day lookback — no clamp
