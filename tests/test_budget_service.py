"""Tests for api.services.budget — daily LLM token budget enforcement.

Hits real Postgres (same as Phase 5d evaluator tests). Each test seeds
rows in `llm_extractions` and/or `llm_query_dispatches` with explicit
`created_at` values to exercise the CR-midnight cutoff. Cleanup is
handled by the `db_with_user` fixture's teardown loop, but this test
file inserts into tables not in `_CLEANUP_TABLES`, so we DELETE them
here on teardown.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import text

from api.config import settings
from api.models.llm_extraction import LLMExtraction
from api.models.llm_query_dispatch import LLMQueryDispatch
from api.services.budget import (
    DEFAULT_QUERY_COST_BUFFER,
    assert_within_budget,
    current_daily_spend,
)
from app.queries.delivery import BudgetExceeded


CR = ZoneInfo("America/Costa_Rica")


def _today_cr_midnight_utc() -> datetime:
    now_local = datetime.now(CR)
    return now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(
        timezone.utc
    )


def _yesterday_cr_evening_utc() -> datetime:
    """A timestamp clearly before today's local midnight."""
    return _today_cr_midnight_utc() - timedelta(hours=2)


@pytest_asyncio.fixture
async def cleanup_llm_rows(db_with_user):
    session, user_id = db_with_user
    yield session, user_id
    # Teardown: clear our seeded rows so the next test starts clean.
    await session.execute(
        text("DELETE FROM llm_query_dispatches WHERE user_id = :u"),
        {"u": user_id},
    )
    await session.execute(
        text("DELETE FROM llm_extractions WHERE user_id = :u"),
        {"u": user_id},
    )
    await session.commit()


# ── current_daily_spend ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_activity_returns_zero(cleanup_llm_rows):
    session, user_id = cleanup_llm_rows
    spent = await current_daily_spend(user_id=user_id, db=session)
    assert spent == 0


@pytest.mark.asyncio
async def test_extractor_only_today(cleanup_llm_rows):
    session, user_id = cleanup_llm_rows
    now = datetime.now(timezone.utc)
    session.add(
        LLMExtraction(
            user_id=user_id,
            message_hash="a" * 64,
            intent="log_expense",
            extraction={},
            latency_ms=10,
            input_tokens=1000,
            output_tokens=200,
            cache_read_tokens=5000,  # MUST NOT be counted
            cache_creation_tokens=300,
            model="claude-haiku-4-5",
            created_at=now,
        )
    )
    await session.commit()

    spent = await current_daily_spend(user_id=user_id, db=session)
    assert spent == 1200  # 1000 + 200, cache_read_tokens excluded


@pytest.mark.asyncio
async def test_query_dispatcher_only_today(cleanup_llm_rows):
    session, user_id = cleanup_llm_rows
    now = datetime.now(timezone.utc)
    session.add(
        LLMQueryDispatch(
            user_id=user_id,
            message_hash="b" * 64,
            total_iterations=2,
            total_input_tokens=1500,
            total_output_tokens=400,
            cache_read_input_tokens=8000,  # MUST NOT count
            cache_creation_input_tokens=2000,
            tools_used=[],
            duration_ms=2000,
            created_at=now,
        )
    )
    await session.commit()

    spent = await current_daily_spend(user_id=user_id, db=session)
    assert spent == 1900


@pytest.mark.asyncio
async def test_both_dispatchers_summed(cleanup_llm_rows):
    session, user_id = cleanup_llm_rows
    now = datetime.now(timezone.utc)
    session.add(
        LLMExtraction(
            user_id=user_id,
            message_hash="a" * 64,
            intent="log_expense",
            extraction={},
            latency_ms=10,
            input_tokens=500,
            output_tokens=100,
            model="claude-haiku-4-5",
            created_at=now,
        )
    )
    session.add(
        LLMQueryDispatch(
            user_id=user_id,
            message_hash="b" * 64,
            total_iterations=1,
            total_input_tokens=2000,
            total_output_tokens=300,
            tools_used=[],
            duration_ms=1500,
            created_at=now,
        )
    )
    await session.commit()

    spent = await current_daily_spend(user_id=user_id, db=session)
    assert spent == 500 + 100 + 2000 + 300


@pytest.mark.asyncio
async def test_yesterday_rows_excluded(cleanup_llm_rows):
    """Reset behavior: rows before today's CR-midnight don't count."""
    session, user_id = cleanup_llm_rows
    yesterday = _yesterday_cr_evening_utc()
    today = datetime.now(timezone.utc)
    session.add(
        LLMQueryDispatch(
            user_id=user_id,
            message_hash="y" * 64,
            total_iterations=1,
            total_input_tokens=50_000,  # huge — but yesterday
            total_output_tokens=10_000,
            tools_used=[],
            duration_ms=2000,
            created_at=yesterday,
        )
    )
    session.add(
        LLMQueryDispatch(
            user_id=user_id,
            message_hash="t" * 64,
            total_iterations=1,
            total_input_tokens=1_500,
            total_output_tokens=300,
            tools_used=[],
            duration_ms=2000,
            created_at=today,
        )
    )
    await session.commit()

    spent = await current_daily_spend(user_id=user_id, db=session)
    assert spent == 1_800  # only today's row


@pytest.mark.asyncio
async def test_other_users_excluded(cleanup_llm_rows):
    """Budget is per-user — another user's spend doesn't count."""
    session, user_id = cleanup_llm_rows

    # Insert a temp user + their dispatch row, then verify our user's
    # spend is unaffected. Use the ORM so column defaults populate
    # without us repeating every NOT NULL field by hand.
    from api.models.user import User
    other = User(
        email=f"other-{uuid.uuid4().hex}@example.com",
        full_name="Other",
        shortcut_token=uuid.uuid4().hex,
    )
    session.add(other)
    await session.commit()
    await session.refresh(other)
    other_id = other.id
    now = datetime.now(timezone.utc)
    session.add(
        LLMQueryDispatch(
            user_id=other_id,
            message_hash="o" * 64,
            total_iterations=1,
            total_input_tokens=99_000,
            total_output_tokens=1_000,
            tools_used=[],
            duration_ms=2000,
            created_at=now,
        )
    )
    await session.commit()

    spent = await current_daily_spend(user_id=user_id, db=session)
    assert spent == 0

    # Cleanup the other user (cleanup_llm_rows only handles our user_id).
    await session.execute(
        text("DELETE FROM llm_query_dispatches WHERE user_id = :u"),
        {"u": other_id},
    )
    await session.execute(
        text("DELETE FROM users WHERE id = :u"), {"u": other_id}
    )
    await session.commit()


# ── assert_within_budget ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assert_passes_when_well_under_cap(cleanup_llm_rows):
    session, user_id = cleanup_llm_rows
    spent = await assert_within_budget(
        user_id=user_id, db=session, buffer_tokens=0
    )
    assert spent == 0


@pytest.mark.asyncio
async def test_assert_passes_with_buffer_exact(cleanup_llm_rows, monkeypatch):
    """Exactly cap - buffer - 1 spent → passes (one token of headroom)."""
    session, user_id = cleanup_llm_rows
    monkeypatch.setattr(settings, "llm_daily_token_budget_per_user", 100_000)
    now = datetime.now(timezone.utc)
    session.add(
        LLMQueryDispatch(
            user_id=user_id,
            message_hash="z" * 64,
            total_iterations=1,
            total_input_tokens=80_000,
            total_output_tokens=17_999,  # 97,999 spent
            tools_used=[],
            duration_ms=2000,
            created_at=now,
        )
    )
    await session.commit()

    # 97,999 + 2000 = 99,999 → still under 100,000.
    spent = await assert_within_budget(
        user_id=user_id, db=session, buffer_tokens=DEFAULT_QUERY_COST_BUFFER
    )
    assert spent == 97_999


@pytest.mark.asyncio
async def test_assert_raises_when_buffer_pushes_over(cleanup_llm_rows, monkeypatch):
    session, user_id = cleanup_llm_rows
    monkeypatch.setattr(settings, "llm_daily_token_budget_per_user", 100_000)
    now = datetime.now(timezone.utc)
    session.add(
        LLMQueryDispatch(
            user_id=user_id,
            message_hash="z" * 64,
            total_iterations=1,
            total_input_tokens=90_000,
            total_output_tokens=8_000,  # 98,000 — buffer of 2000 = 100,000
            tools_used=[],
            duration_ms=2000,
            created_at=now,
        )
    )
    await session.commit()

    with pytest.raises(BudgetExceeded):
        await assert_within_budget(
            user_id=user_id, db=session, buffer_tokens=DEFAULT_QUERY_COST_BUFFER
        )


@pytest.mark.asyncio
async def test_assert_raises_at_cap(cleanup_llm_rows, monkeypatch):
    session, user_id = cleanup_llm_rows
    monkeypatch.setattr(settings, "llm_daily_token_budget_per_user", 100_000)
    now = datetime.now(timezone.utc)
    session.add(
        LLMQueryDispatch(
            user_id=user_id,
            message_hash="z" * 64,
            total_iterations=1,
            total_input_tokens=99_000,
            total_output_tokens=1_500,
            tools_used=[],
            duration_ms=2000,
            created_at=now,
        )
    )
    await session.commit()

    with pytest.raises(BudgetExceeded) as ei:
        await assert_within_budget(user_id=user_id, db=session, buffer_tokens=0)
    msg = str(ei.value)
    assert "100500" in msg.replace(",", "") or "100,500" in msg


@pytest.mark.asyncio
async def test_assert_disabled_when_cap_zero(cleanup_llm_rows, monkeypatch):
    """cap <= 0 disables enforcement — useful for tests."""
    session, user_id = cleanup_llm_rows
    monkeypatch.setattr(settings, "llm_daily_token_budget_per_user", 0)
    now = datetime.now(timezone.utc)
    session.add(
        LLMQueryDispatch(
            user_id=user_id,
            message_hash="z" * 64,
            total_iterations=1,
            total_input_tokens=1_000_000,  # absurd
            total_output_tokens=100_000,
            tools_used=[],
            duration_ms=2000,
            created_at=now,
        )
    )
    await session.commit()

    spent = await assert_within_budget(user_id=user_id, db=session)
    # When disabled we skip the SELECT entirely → returns 0 not the
    # actual spend. This is documented behavior.
    assert spent == 0
