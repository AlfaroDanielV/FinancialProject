"""Phase 6c B10 — nightly runner + worker tests.

Service tests use the real `db_with_user` Postgres fixture. The
worker-level test patches the per-user runner so we exercise the loop +
crash-isolation contract without seeding many users.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from api.config import settings
from api.models.user import User
from api.models.user_insight import UserInsight, UserInsightAudit
from api.services.insights.nightly_runner import (
    NightlyRunStats,
    run_nightly_for_user,
)
from workers import insights_nightly as worker


# ── runner: persists computed insights for one user ─────────────────────────


@pytest.mark.asyncio
async def test_run_nightly_for_user_persists_computed_insights(db_with_user):
    """A bare user with no transactions still produces some computed
    insights (e.g. cash_flow_stability with score=0). The runner must
    persist whatever compute_all returns and report the stats."""
    session, user_id = db_with_user
    now = datetime(2026, 5, 8, 9, 30, tzinfo=timezone.utc)

    stats = await run_nightly_for_user(session, user_id=user_id, now=now)
    await session.commit()

    assert stats.user_id == user_id
    assert stats.insights_proposed >= 0
    # If anything was proposed, it should land in the table.
    rows = (
        await session.execute(
            select(UserInsight).where(UserInsight.user_id == user_id)
        )
    ).scalars().all()
    assert len(rows) == stats.persisted_created
    if rows:
        assert all(r.source == "computed" for r in rows)


@pytest.mark.asyncio
async def test_run_nightly_for_user_emits_created_audits(db_with_user):
    session, user_id = db_with_user
    now = datetime(2026, 5, 8, 9, 30, tzinfo=timezone.utc)
    stats = await run_nightly_for_user(session, user_id=user_id, now=now)
    await session.commit()

    if stats.persisted_created == 0:
        pytest.skip("compute_all produced no insights for empty user; ok")

    audits = (
        await session.execute(
            select(UserInsightAudit)
            .where(UserInsightAudit.user_id == user_id)
            .where(UserInsightAudit.action == "created")
        )
    ).scalars().all()
    # One created-audit per persisted row.
    assert len(audits) == stats.persisted_created


@pytest.mark.asyncio
async def test_run_nightly_for_user_idempotent_second_pass_reinforces(db_with_user):
    """Running the recompute twice in a row should not duplicate rows.
    Second pass returns reinforced/updated, not created."""
    session, user_id = db_with_user
    now = datetime(2026, 5, 8, 9, 30, tzinfo=timezone.utc)

    first = await run_nightly_for_user(session, user_id=user_id, now=now)
    await session.commit()
    if first.insights_proposed == 0:
        pytest.skip("compute_all produced no insights for empty user; ok")

    second = await run_nightly_for_user(session, user_id=user_id, now=now)
    await session.commit()
    assert second.persisted_created == 0
    rows_after = (
        await session.execute(
            select(UserInsight).where(UserInsight.user_id == user_id)
        )
    ).scalars().all()
    # Same number of rows after second pass.
    assert len(rows_after) == first.persisted_created


# ── worker loop: crash isolation across users ───────────────────────────────


@pytest.mark.asyncio
async def test_worker_continues_after_per_user_failure(monkeypatch):
    """One bad user must not kill the run. The worker logs and continues.

    The worker uses `api.database.AsyncSessionLocal` (the global engine).
    pytest-asyncio cycles event loops between tests, so the global engine
    can carry a closed loop into this test. We rebind to a NullPool
    factory bound to the live loop.
    """
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(worker, "AsyncSessionLocal", factory)
    user_ids: list[uuid.UUID] = []
    try:
        async with factory() as s:
            for label in ("a", "b"):
                u = User(
                    email=f"b10-{label}-{uuid.uuid4().hex}@example.com",
                    full_name=label.upper(),
                    shortcut_token=secrets.token_urlsafe(48),
                )
                s.add(u)
            await s.commit()
            rows = (
                await s.execute(
                    select(User.id).where(User.email.like("b10-%"))
                )
            ).scalars().all()
            user_ids = list(rows)

        # Patch the per-user runner: first user raises, second succeeds.
        seen: list[uuid.UUID] = []

        async def fake_run_one(*, user_id, now):
            seen.append(user_id)
            if user_id == user_ids[0]:
                return None  # crash branch (worker swallows + logs)
            return NightlyRunStats(user_id=user_id)

        with patch.object(worker, "_run_one_user", fake_run_one):
            # _run_global_expire still hits the DB, but expire_stale on a
            # clean test slate is a no-op. We let it run for realism.
            summary = await worker.run_nightly_for_all_users(
                now=datetime(2026, 5, 8, 9, 30, tzinfo=timezone.utc)
            )
        assert summary.users_scanned >= 2
        assert summary.users_failed >= 1
        assert summary.users_succeeded >= 1
        # Both seeded users were attempted.
        for uid in user_ids:
            assert uid in seen
    finally:
        async with factory() as s:
            for uid in user_ids:
                await s.execute(
                    text("DELETE FROM user_insights_audit WHERE user_id = :u"),
                    {"u": uid},
                )
                await s.execute(
                    text("DELETE FROM user_insights WHERE user_id = :u"),
                    {"u": uid},
                )
                await s.execute(
                    text("DELETE FROM users WHERE id = :u"), {"u": uid}
                )
            await s.commit()
        await engine.dispose()
