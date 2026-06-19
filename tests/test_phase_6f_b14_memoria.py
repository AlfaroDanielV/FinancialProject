"""Phase 6f B14 — Memoria (insights) backend contracts.

Covers:
1. GET /users/me/insights returns grouped insights.
2. GET /users/me/insights/export returns JSON export payload.
3. DELETE /users/me/insights/{id} hard-deletes one insight.
4. DELETE /users/me/insights/group/{group} deletes all insights in a group.
5. DELETE /users/me/insights deletes all insights (privacy delete-all).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select as sa_select

from api.database import get_db
from api.dependencies import current_user, current_user_via_token
from api.main import app
from api.models.user_insight import UserInsight


def _override(session, user_id):
    class _StubUser:
        def __init__(self) -> None:
            self.id = user_id
            self.status = "active"
            self.currency = "CRC"
            self.display_currency = "CRC"

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user] = lambda: _StubUser()
    app.dependency_overrides[current_user_via_token] = lambda: _StubUser()
    app.dependency_overrides[get_db] = _yield_session


def _clear():
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(current_user_via_token, None)
    app.dependency_overrides.pop(get_db, None)


async def _seed_insight(
    session,
    user_id,
    *,
    insight_type: str = "spending_pattern",
    content: dict,
    confidence: str = "0.80",
) -> str:
    now = datetime.now(timezone.utc)
    row = UserInsight(
        user_id=user_id,
        insight_type=insight_type,
        dedup_key=str(uuid.uuid4()),
        content=content,
        confidence=Decimal(confidence),
        source="computed",
        user_locked=False,
        valid_until=now + timedelta(days=30),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return str(row.id)


# ── 1. GET returns grouped insights ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_insights_grouped(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        await _seed_insight(
            session,
            user_id,
            insight_type="spending_pattern",
            content={
                "type": "spending_pattern",
                "category": "Alimentación",
                "monthly_avg_crc": 150000,
                "trend_pct_3mo": 8.0,
                "volatility_score": 0.3,
            },
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/users/me/insights")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "total" in body
            assert "groups" in body
            assert body["total"] >= 1
    finally:
        _clear()


# ── 2. GET /export returns JSON payload ───────────────────────────────────────


@pytest.mark.asyncio
async def test_export_insights_returns_json(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        await _seed_insight(
            session,
            user_id,
            insight_type="spending_pattern",
            content={
                "type": "spending_pattern",
                "category": "Transporte",
                "monthly_avg_crc": 50000,
                "trend_pct_3mo": -3.0,
                "volatility_score": 0.2,
            },
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/users/me/insights/export")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "exported_at" in body
            assert "insights" in body
    finally:
        _clear()


# ── 3. DELETE one insight ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_one_insight(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        insight_id = await _seed_insight(
            session,
            user_id,
            insight_type="spending_pattern",
            content={
                "type": "spending_pattern",
                "category": "Ocio",
                "monthly_avg_crc": 30000,
                "trend_pct_3mo": 1.5,
                "volatility_score": 0.4,
            },
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(f"/api/v1/users/me/insights/{insight_id}")
            assert resp.status_code == 200, resp.text
            assert resp.json()["deleted"] == 1

            # Confirm it's gone
            list_resp = await ac.get("/api/v1/users/me/insights")
            all_ids = [
                item["id"]
                for g in list_resp.json()["groups"]
                for item in g["items"]
            ]
            assert insight_id not in all_ids
    finally:
        _clear()


# ── 4. DELETE group ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_group_insights(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        # Seed two "patrones" insights
        await _seed_insight(
            session,
            user_id,
            insight_type="spending_pattern",
            content={
                "type": "spending_pattern",
                "category": "Salud",
                "monthly_avg_crc": 40000,
                "trend_pct_3mo": 0.0,
                "volatility_score": 0.1,
            },
        )
        await _seed_insight(
            session,
            user_id,
            insight_type="spending_pattern",
            content={
                "type": "spending_pattern",
                "category": "Ropa",
                "monthly_avg_crc": 25000,
                "trend_pct_3mo": 5.0,
                "volatility_score": 0.5,
            },
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete("/api/v1/users/me/insights/group/patrones")
            assert resp.status_code == 200, resp.text
            assert resp.json()["deleted"] >= 2
    finally:
        _clear()


# ── 5. DELETE all insights ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_all_insights(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        await _seed_insight(
            session,
            user_id,
            insight_type="spending_pattern",
            content={
                "type": "spending_pattern",
                "category": "Viajes",
                "monthly_avg_crc": 80000,
                "trend_pct_3mo": -2.0,
                "volatility_score": 0.25,
            },
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete("/api/v1/users/me/insights")
            assert resp.status_code == 200, resp.text
            assert resp.json()["deleted"] >= 1

            list_resp = await ac.get("/api/v1/users/me/insights")
            assert list_resp.json()["total"] == 0
    finally:
        _clear()
