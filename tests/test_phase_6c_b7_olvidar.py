"""Phase 6c B7 — /olvidar service + handler tests.

Service tests run against real Postgres via the `db_with_user` fixture.
Handler tests use the same fake-aiogram pattern as test_clear_command.py.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from api.config import settings
from api.models.user import User
from api.models.user_insight import UserInsight, UserInsightAudit
from api.schemas.insights import (
    SpendingPatternContent,
    StatedGoalContent,
    StatedPreferenceContent,
)
from api.services.insights.memory_view import delete_insights
from api.services.insights.persister import persist_insights
from bot import memory_handlers, messages_es


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


async def _seed(session, user_id: uuid.UUID, content, *, confidence: Decimal):
    setattr(content, "_llm_confidence", confidence)
    await persist_insights(
        session,
        user_id=user_id,
        insights=[content],
        source="llm_extracted" if hasattr(content, "raw_quote") else "computed",
        now=_utc(2026, 5, 8),
    )


# ── service: delete_insights ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_insights_single_writes_audit_and_removes_row(db_with_user):
    session, user_id = db_with_user
    content = StatedGoalContent(
        goal_text="Ahorrar para el marchamo",
        target_amount=Decimal("500000"),
        target_date=None,
        status="committed",
    )
    setattr(content, "_llm_confidence", Decimal("0.80"))
    await persist_insights(
        session,
        user_id=user_id,
        insights=[content],
        source="llm_extracted",
        now=_utc(2026, 5, 8),
    )
    await session.commit()

    row = (
        await session.execute(select(UserInsight).where(UserInsight.user_id == user_id))
    ).scalar_one()

    deleted = await delete_insights(
        session,
        user_id=user_id,
        insight_ids=[row.id],
        deletion_reason="user_olvidar",
    )
    await session.commit()

    assert deleted == 1
    remaining = (
        await session.execute(select(UserInsight).where(UserInsight.user_id == user_id))
    ).scalars().all()
    assert remaining == []

    audits = (
        await session.execute(
            select(UserInsightAudit)
            .where(UserInsightAudit.user_id == user_id)
            .where(UserInsightAudit.action == "deleted")
        )
    ).scalars().all()
    # One created + one deleted; we asked for the deleted only.
    assert len(audits) == 1
    payload = audits[0].payload
    assert payload["action"] == "deleted"
    assert payload["insight_type"] == "stated_goal"
    assert payload["deletion_reason"] == "user_olvidar"


@pytest.mark.asyncio
async def test_delete_insights_bulk_writes_one_audit_per_row(db_with_user):
    session, user_id = db_with_user
    contents = [
        StatedGoalContent(
            goal_text="Meta uno",
            target_amount=None,
            target_date=None,
            status="mentioned",
        ),
        SpendingPatternContent(
            category="supermercado",
            monthly_avg_crc=Decimal("100000"),
            monthly_avg_usd=None,
            trend_pct_3mo=Decimal("0"),
            volatility_score=Decimal("0.10"),
        ),
    ]
    for c in contents:
        if hasattr(c, "raw_quote"):
            setattr(c, "_llm_confidence", Decimal("0.80"))
        source = "llm_extracted" if c.type in {"stated_goal"} else "computed"
        await persist_insights(
            session,
            user_id=user_id,
            insights=[c],
            source=source,
            now=_utc(2026, 5, 8),
        )
    await session.commit()

    deleted = await delete_insights(
        session,
        user_id=user_id,
        deletion_reason="user_olvidar_todo",
    )
    await session.commit()

    assert deleted == 2
    remaining = (
        await session.execute(select(UserInsight).where(UserInsight.user_id == user_id))
    ).scalars().all()
    assert remaining == []

    audits = (
        await session.execute(
            select(UserInsightAudit)
            .where(UserInsightAudit.user_id == user_id)
            .where(UserInsightAudit.action == "deleted")
        )
    ).scalars().all()
    assert len(audits) == 2
    for a in audits:
        assert a.payload["deletion_reason"] == "user_olvidar_todo"


@pytest.mark.asyncio
async def test_delete_insights_redacts_raw_quote_in_audit(db_with_user):
    session, user_id = db_with_user
    content = StatedPreferenceContent(
        topic="savings",
        stance="quiere ahorrar más cada mes",
        raw_quote="Tengo que apartar plata todos los meses sí o sí.",
        extracted_at=_utc(2026, 5, 8),
    )
    setattr(content, "_llm_confidence", Decimal("0.80"))
    await persist_insights(
        session,
        user_id=user_id,
        insights=[content],
        source="llm_extracted",
        now=_utc(2026, 5, 8),
    )
    await session.commit()

    row = (
        await session.execute(select(UserInsight).where(UserInsight.user_id == user_id))
    ).scalar_one()
    await delete_insights(
        session,
        user_id=user_id,
        insight_ids=[row.id],
        deletion_reason="user_olvidar",
    )
    await session.commit()

    audit_row = (
        await session.execute(
            select(UserInsightAudit)
            .where(UserInsightAudit.user_id == user_id)
            .where(UserInsightAudit.action == "deleted")
        )
    ).scalar_one()
    snapshot = audit_row.payload["content_at_deletion"]
    assert snapshot["raw_quote"] == "[eliminado por privacidad]"
    # Other fields preserved.
    assert snapshot["topic"] == "savings"


@pytest.mark.asyncio
async def test_delete_insights_empty_user_returns_zero(db_with_user):
    session, user_id = db_with_user
    deleted = await delete_insights(
        session,
        user_id=user_id,
        deletion_reason="user_olvidar_todo",
    )
    assert deleted == 0


@pytest.mark.asyncio
async def test_delete_insights_cross_user_safety_via_id_filter():
    """Two real users; delete_insights with user_id=A and an id from B
    must return 0 and not touch B's row."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    user_a_id: uuid.UUID | None = None
    user_b_id: uuid.UUID | None = None
    insight_b_id: uuid.UUID | None = None
    try:
        async with factory() as s:
            ua = User(
                email=f"olv-a-{uuid.uuid4().hex}@example.com",
                full_name="A",
                shortcut_token=secrets.token_urlsafe(48),
            )
            ub = User(
                email=f"olv-b-{uuid.uuid4().hex}@example.com",
                full_name="B",
                shortcut_token=secrets.token_urlsafe(48),
            )
            s.add_all([ua, ub])
            await s.commit()
            await s.refresh(ua)
            await s.refresh(ub)
            user_a_id = ua.id
            user_b_id = ub.id

            content_b = StatedGoalContent(
                goal_text="Meta de B",
                target_amount=None,
                target_date=None,
                status="mentioned",
            )
            setattr(content_b, "_llm_confidence", Decimal("0.80"))
            await persist_insights(
                s,
                user_id=ub.id,
                insights=[content_b],
                source="llm_extracted",
                now=_utc(2026, 5, 8),
            )
            await s.commit()
            row_b = (
                await s.execute(
                    select(UserInsight).where(UserInsight.user_id == ub.id)
                )
            ).scalar_one()
            insight_b_id = row_b.id

            # Try to delete B's insight as user A.
            deleted = await delete_insights(
                s,
                user_id=ua.id,
                insight_ids=[row_b.id],
                deletion_reason="user_olvidar",
            )
            await s.commit()
            assert deleted == 0

            # B's row still exists.
            still_there = (
                await s.execute(
                    select(UserInsight).where(UserInsight.id == row_b.id)
                )
            ).scalar_one_or_none()
            assert still_there is not None
    finally:
        async with factory() as s:
            for uid in (user_a_id, user_b_id):
                if uid is None:
                    continue
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


# ── handler: /olvidar (no args) replies with menu when there's data ─────────


class _FakeFromUser:
    def __init__(self, tg_id: int) -> None:
        self.id = tg_id


class _FakeMessage:
    def __init__(self, from_user_id: int) -> None:
        self.from_user = _FakeFromUser(from_user_id)
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text_: str, reply_markup=None) -> None:
        self.answers.append((text_, reply_markup))


class _FakeUser:
    def __init__(self, user_id: uuid.UUID) -> None:
        self.id = user_id


class _FakeCmd:
    def __init__(self, args: str | None = None) -> None:
        self.args = args


@pytest.mark.asyncio
async def test_handler_olvidar_no_data_replies_nothing_to_delete():
    """If the user has zero active insights, the handler short-circuits
    with the OLVIDAR_TODO_NOTHING message and does NOT show the menu."""
    user_id = uuid.uuid4()
    fake_user = _FakeUser(user_id)
    msg = _FakeMessage(from_user_id=42)

    async def fake_user_lookup(*, telegram_user_id, db):
        return fake_user

    async def fake_load(*args, **kwargs):
        return []

    with (
        patch.object(memory_handlers, "AsyncSessionLocal", lambda: AsyncMock()),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
        patch.object(memory_handlers, "load_active_insights", fake_load),
    ):
        await memory_handlers.on_olvidar(msg, _FakeCmd())

    assert msg.answers
    text_, _kb = msg.answers[-1]
    assert text_ == messages_es.OLVIDAR_TODO_NOTHING


@pytest.mark.asyncio
async def test_handler_olvidar_unpaired_user_gets_pair_prompt():
    msg = _FakeMessage(from_user_id=99)

    async def fake_user_lookup(**kwargs):
        return None

    with (
        patch.object(memory_handlers, "AsyncSessionLocal", lambda: AsyncMock()),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
    ):
        await memory_handlers.on_olvidar(msg, _FakeCmd())

    assert msg.answers == [(messages_es.PAIR_PROMPT, None)]
