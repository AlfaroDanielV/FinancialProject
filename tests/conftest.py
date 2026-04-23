"""Pytest config shared across the test suite.

Phase 5b tests (test_llm_extractor, test_telegram_dispatcher) are DB-free
by design — they use fakes. Phase 5d evaluator tests, however, are really
SQL — stubbing the session defeats the point. Those use the `db_with_user`
fixture which connects to the running Postgres (same one docker-compose
boots), inserts a throw-away user, yields an AsyncSession, and cleans
everything up in FK order on teardown.

Run prerequisites for evaluator tests:
    docker compose up -d db
    alembic upgrade head
"""
from __future__ import annotations

import secrets
import sys
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── async DB fixture for Phase 5d evaluator / orchestrator tests ──────────────
# Tables touched by evaluators + orchestrator, in deletion order (children
# first). user_id cascades on most, but transactions / notification_events
# use RESTRICT so we must clean them ourselves before deleting the user.
_CLEANUP_TABLES = (
    "user_nudges",
    "user_nudge_silences",
    "pending_confirmations",
    "notification_events",
    "bill_occurrences",
    "recurring_bills",
    "custom_events",
    "notification_rules",
    "transactions",
    "accounts",
)


@pytest_asyncio.fixture
async def db_with_user() -> AsyncGenerator[tuple[AsyncSession, uuid.UUID], None]:
    """Yields (session, user_id). Cleans up on teardown.

    Each test gets its own engine with NullPool so we never try to reuse
    a connection across event loops — pytest-asyncio creates a fresh loop
    per function by default, which breaks the app's shared pool. Engine
    disposal at teardown closes connections cleanly on the current loop.
    """
    from api.config import settings
    from api.models.user import User

    engine = create_async_engine(
        settings.database_url, poolclass=NullPool
    )
    session_factory = async_sessionmaker(
        bind=engine, expire_on_commit=False, autoflush=False
    )

    async with session_factory() as session:
        user = User(
            email=f"nudge-test-{uuid.uuid4().hex}@example.com",
            full_name="Nudge Test",
            shortcut_token=secrets.token_urlsafe(48),
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id
        try:
            yield session, user_id
        finally:
            await session.rollback()
            for table in _CLEANUP_TABLES:
                await session.execute(
                    text(f"DELETE FROM {table} WHERE user_id = :u"),
                    {"u": user_id},
                )
            await session.execute(
                text("DELETE FROM users WHERE id = :u"), {"u": user_id}
            )
            await session.commit()
    await engine.dispose()
