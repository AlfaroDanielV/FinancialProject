"""Tests for workers.gmail_daily.

We exercise:
  - `_last_successful_since`: returns the started_at of the most recent
    finished run, or None.
  - `run_daily_for_all_users`: iterates active credentials only,
    invokes scan + notify per user, swallows per-user exceptions.

The scanner and notifier are stubbed via monkeypatch; the worker logic
in isolation is what we care about here.
"""
from __future__ import annotations

import socket
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import pytest
import pytest_asyncio

from api.config import settings
from api.models.gmail_credential import GmailCredential
from api.models.gmail_ingestion_run import GmailIngestionRun
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


@pytest_asyncio.fixture
async def patched_session_factory(db_with_user, monkeypatch):
    """The daily worker imports AsyncSessionLocal from api.database — a
    module-level singleton bound to the loop that imported it. Across
    pytest-asyncio's per-function event loops that's stale after the
    first test. Patch it to a fresh sessionmaker on a per-test engine
    using NullPool, mirroring conftest's db_with_user pattern."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from workers import gmail_daily

    db, user_id = db_with_user
    fresh_engine = create_async_engine(
        settings.database_url, poolclass=NullPool
    )
    fresh_factory = async_sessionmaker(
        bind=fresh_engine, expire_on_commit=False, autoflush=False
    )
    monkeypatch.setattr(gmail_daily, "AsyncSessionLocal", fresh_factory)
    try:
        yield db, user_id
    finally:
        await fresh_engine.dispose()


# ── _last_successful_since ───────────────────────────────────────────────────


async def test_last_successful_since_returns_none_when_no_runs(db_with_user):
    from workers import gmail_daily

    db, user_id = db_with_user
    out = await gmail_daily._last_successful_since(db=db, user_id=user_id)
    assert out is None


async def test_last_successful_since_returns_started_at_of_most_recent_finished(
    db_with_user,
):
    from workers import gmail_daily

    db, user_id = db_with_user
    older = GmailIngestionRun(
        user_id=user_id,
        mode="backfill",
        started_at=datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 5, 1, 0, 5, tzinfo=timezone.utc),
    )
    newer = GmailIngestionRun(
        user_id=user_id,
        mode="daily",
        started_at=datetime(2026, 5, 5, 0, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 5, 5, 0, 4, tzinfo=timezone.utc),
    )
    unfinished = GmailIngestionRun(
        user_id=user_id,
        mode="daily",
        started_at=datetime(2026, 5, 6, 0, 0, tzinfo=timezone.utc),
        finished_at=None,  # didn't complete — should be ignored
    )
    db.add_all([older, newer, unfinished])
    await db.commit()

    out = await gmail_daily._last_successful_since(db=db, user_id=user_id)
    assert out is not None
    # Most recent FINISHED run = May 5, not the unfinished May 6.
    assert out.replace(tzinfo=timezone.utc).date() == newer.started_at.date()


# ── run_daily_for_all_users ──────────────────────────────────────────────────


async def test_run_daily_iterates_only_active_users(
    patched_session_factory, monkeypatch
):
    """Active = activated_at IS NOT NULL AND revoked_at IS NULL."""
    from workers import gmail_daily

    db, active_user_id = patched_session_factory
    # Active row.
    db.add(
        GmailCredential(
            user_id=active_user_id,
            kv_secret_name=f"gmail-refresh-{active_user_id}",
            scopes=[],
            granted_at=datetime.now(timezone.utc),
            activated_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
    )
    await db.commit()

    # Stub the scan + notify so we don't touch Gmail / Telegram.
    scanned: list[uuid.UUID] = []
    notified: list[uuid.UUID] = []
    summaries: list[uuid.UUID] = []

    async def fake_scan(*, user_id, since, until, mode, db, **kwargs):
        scanned.append(user_id)
        return ScanResult(
            user_id=user_id,
            mode=mode,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )

    async def fake_notify_completed(*, user_id, result, db):
        notified.append(user_id)

    async def fake_summary(*, user_id, db, target_date=None):
        summaries.append(user_id)
        return False

    monkeypatch.setattr(gmail_daily, "scan_user_inbox", fake_scan)
    monkeypatch.setattr(
        gmail_daily.notifier_mod, "notify_run_completed", fake_notify_completed
    )
    monkeypatch.setattr(
        gmail_daily.notifier_mod, "maybe_send_shadow_summary", fake_summary
    )

    await gmail_daily.run_daily_for_all_users()

    assert active_user_id in scanned
    assert active_user_id in notified
    assert active_user_id in summaries


async def test_run_daily_skips_revoked_users(
    patched_session_factory, monkeypatch
):
    from workers import gmail_daily

    db, user_id = patched_session_factory
    db.add(
        GmailCredential(
            user_id=user_id,
            kv_secret_name=f"gmail-refresh-{user_id}",
            scopes=[],
            granted_at=datetime.now(timezone.utc),
            activated_at=datetime.now(timezone.utc) - timedelta(days=1),
            revoked_at=datetime.now(timezone.utc),  # revoked
        )
    )
    await db.commit()

    scanned: list[uuid.UUID] = []

    async def fake_scan(**kwargs):
        scanned.append(kwargs["user_id"])
        return ScanResult(
            user_id=kwargs["user_id"],
            mode=kwargs["mode"],
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )

    async def noop(**kwargs):
        return False

    monkeypatch.setattr(gmail_daily, "scan_user_inbox", fake_scan)
    monkeypatch.setattr(
        gmail_daily.notifier_mod, "notify_run_completed", noop
    )
    monkeypatch.setattr(
        gmail_daily.notifier_mod, "maybe_send_shadow_summary", noop
    )

    await gmail_daily.run_daily_for_all_users()
    # The test user (revoked) must not appear. Other pre-existing rows
    # in this DB may still be processed — we only assert the contract.
    assert user_id not in scanned


async def test_run_daily_skips_unactivated_users(
    patched_session_factory, monkeypatch
):
    from workers import gmail_daily

    db, user_id = patched_session_factory
    db.add(
        GmailCredential(
            user_id=user_id,
            kv_secret_name=f"gmail-refresh-{user_id}",
            scopes=[],
            granted_at=datetime.now(timezone.utc),
            activated_at=None,  # never activated
        )
    )
    await db.commit()

    scanned: list[uuid.UUID] = []

    async def fake_scan(**kwargs):
        scanned.append(kwargs["user_id"])
        return ScanResult(
            user_id=kwargs["user_id"],
            mode=kwargs["mode"],
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )

    async def noop(**kwargs):
        return False

    monkeypatch.setattr(gmail_daily, "scan_user_inbox", fake_scan)
    monkeypatch.setattr(
        gmail_daily.notifier_mod, "notify_run_completed", noop
    )
    monkeypatch.setattr(
        gmail_daily.notifier_mod, "maybe_send_shadow_summary", noop
    )

    await gmail_daily.run_daily_for_all_users()
    # Same exclusion contract as the revoked test.
    assert user_id not in scanned


async def test_run_daily_swallows_per_user_exceptions(
    patched_session_factory, monkeypatch
):
    """One user's failure must not abort the run for other users."""
    from workers import gmail_daily

    db, user_a = patched_session_factory
    # Active credential for our user.
    db.add(
        GmailCredential(
            user_id=user_a,
            kv_secret_name=f"gmail-refresh-{user_a}",
            scopes=[],
            granted_at=datetime.now(timezone.utc),
            activated_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
    )
    await db.commit()

    boom_for_test_user = {"n": 0}

    async def fake_scan(**kwargs):
        if kwargs["user_id"] == user_a:
            boom_for_test_user["n"] += 1
            raise RuntimeError("simulated scanner crash")
        # Other pre-existing rows: succeed silently.
        return ScanResult(
            user_id=kwargs["user_id"],
            mode=kwargs["mode"],
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )

    async def noop(**kwargs):
        return False

    monkeypatch.setattr(gmail_daily, "scan_user_inbox", fake_scan)
    monkeypatch.setattr(
        gmail_daily.notifier_mod, "notify_run_completed", noop
    )
    monkeypatch.setattr(
        gmail_daily.notifier_mod, "maybe_send_shadow_summary", noop
    )

    # Should not raise — the worker swallows our test user's failure.
    await gmail_daily.run_daily_for_all_users()
    assert boom_for_test_user["n"] == 1
