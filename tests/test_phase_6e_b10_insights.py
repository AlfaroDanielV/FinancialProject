"""Phase 6e B10 — memoria SPA backend contracts.

Covers:
- GET /api/v1/users/me/insights returns rows grouped by category in
  GROUP_ORDER, with the right item fields.
- PATCH /api/v1/users/me/insights/{id} on an editable type sets
  source='user_override', user_locked=true, confidence=1.00, and emits a
  `locked` audit row.
- PATCH on a computed type rejects with 400.
- PATCH with mismatched content.type rejects with 400.
- DELETE /api/v1/users/me/insights/{id} hard-deletes one row + audit.
- DELETE /api/v1/users/me/insights/group/{group} hard-deletes every row
  in that group; other groups untouched.
- DELETE /api/v1/users/me/insights (all) still works (regression).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select as sa_select

from api.database import get_db
from api.dependencies import current_user, current_user_via_token
from api.main import app
from api.models.user_insight import UserInsight, UserInsightAudit


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
    insight_type: str,
    content: dict,
    confidence: str = "0.80",
    source: str = "llm_extracted",
    user_locked: bool = False,
    dedup_key: str = "global",
):
    now = datetime.now(timezone.utc)
    row = UserInsight(
        id=uuid.uuid4(),
        user_id=user_id,
        insight_type=insight_type,
        content=content,
        dedup_key=dedup_key,
        confidence=Decimal(confidence),
        source=source,
        user_locked=user_locked,
        valid_until=datetime(2030, 1, 1, tzinfo=timezone.utc),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@pytest.mark.asyncio
async def test_list_returns_grouped_rows(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        # One in each group (besides "metas" — see below).
        await _seed_insight(
            session,
            user_id,
            insight_type="risk_posture",
            content={
                "type": "risk_posture",
                "posture": "moderate",
                "evidence_basis": "stated",
            },
        )
        await _seed_insight(
            session,
            user_id,
            insight_type="cash_flow_stability",
            content={
                "type": "cash_flow_stability",
                "score_0_100": 70,
                "monthly_variance_pct": "12.5",
                "income_sources_count": 1,
                "savings_rate_pct": "8.0",
            },
            source="computed",
            confidence="0.95",
        )
        await _seed_insight(
            session,
            user_id,
            insight_type="stated_goal",
            content={
                "type": "stated_goal",
                "goal_text": "Comprar carro usado",
                "status": "mentioned",
            },
            dedup_key="comprar carro usado",
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/users/me/insights")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["total"] == 3

            groups = {g["group"]: g for g in body["groups"]}
            assert "conozco" in groups
            assert "patrones" in groups
            assert "metas" in groups

            risk_item = groups["conozco"]["items"][0]
            assert risk_item["insight_type"] == "risk_posture"
            assert risk_item["editable"] is True
            assert risk_item["source"] == "llm_extracted"

            cash_item = groups["patrones"]["items"][0]
            assert cash_item["editable"] is False
    finally:
        _clear()


@pytest.mark.asyncio
async def test_patch_sets_user_override_and_emits_locked_audit(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        row = await _seed_insight(
            session,
            user_id,
            insight_type="risk_posture",
            content={
                "type": "risk_posture",
                "posture": "moderate",
                "evidence_basis": "stated",
            },
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.patch(
                f"/api/v1/users/me/insights/{row.id}",
                json={
                    "content": {
                        "type": "risk_posture",
                        "posture": "aggressive",
                        "evidence_basis": "stated",
                    }
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["source"] == "user_override"
            assert body["user_locked"] is True
            assert Decimal(str(body["confidence"])) == Decimal("1.00")
            assert body["content"]["posture"] == "aggressive"

            # Locked audit row was written.
            audits = (
                await session.execute(
                    sa_select(UserInsightAudit).where(
                        UserInsightAudit.user_id == user_id,
                        UserInsightAudit.action == "locked",
                    )
                )
            ).scalars().all()
            assert len(audits) >= 1
            assert audits[-1].payload.get("insight_type") == "risk_posture"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_patch_rejects_computed_type(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        row = await _seed_insight(
            session,
            user_id,
            insight_type="cash_flow_stability",
            content={
                "type": "cash_flow_stability",
                "score_0_100": 70,
                "monthly_variance_pct": "12.5",
                "income_sources_count": 1,
                "savings_rate_pct": "8.0",
            },
            source="computed",
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.patch(
                f"/api/v1/users/me/insights/{row.id}",
                json={
                    "content": {
                        "type": "cash_flow_stability",
                        "score_0_100": 90,
                        "monthly_variance_pct": "5.0",
                        "income_sources_count": 2,
                        "savings_rate_pct": "20.0",
                    }
                },
            )
            assert resp.status_code == 400, resp.text
            assert "computado" in resp.json()["detail"]
    finally:
        _clear()


@pytest.mark.asyncio
async def test_patch_rejects_content_type_mismatch(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        row = await _seed_insight(
            session,
            user_id,
            insight_type="risk_posture",
            content={
                "type": "risk_posture",
                "posture": "moderate",
                "evidence_basis": "stated",
            },
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.patch(
                f"/api/v1/users/me/insights/{row.id}",
                json={
                    "content": {
                        "type": "decision_style",
                        "style": "analytical",
                        "evidence": "siempre revisa antes de gastar",
                    }
                },
            )
            assert resp.status_code == 400, resp.text
    finally:
        _clear()


@pytest.mark.asyncio
async def test_delete_single_insight(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        row = await _seed_insight(
            session,
            user_id,
            insight_type="risk_posture",
            content={
                "type": "risk_posture",
                "posture": "moderate",
                "evidence_basis": "stated",
            },
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(f"/api/v1/users/me/insights/{row.id}")
            assert resp.status_code == 200, resp.text
            assert resp.json()["deleted"] == 1

            again = await ac.delete(f"/api/v1/users/me/insights/{row.id}")
            assert again.status_code == 404
    finally:
        _clear()


@pytest.mark.asyncio
async def test_delete_by_group_only_touches_target_group(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        await _seed_insight(
            session,
            user_id,
            insight_type="risk_posture",
            content={
                "type": "risk_posture",
                "posture": "moderate",
                "evidence_basis": "stated",
            },
        )
        await _seed_insight(
            session,
            user_id,
            insight_type="decision_style",
            content={
                "type": "decision_style",
                "style": "analytical",
                "evidence": "revisa todo antes",
            },
        )
        kept = await _seed_insight(
            session,
            user_id,
            insight_type="cash_flow_stability",
            content={
                "type": "cash_flow_stability",
                "score_0_100": 70,
                "monthly_variance_pct": "12.5",
                "income_sources_count": 1,
                "savings_rate_pct": "8.0",
            },
            source="computed",
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(
                "/api/v1/users/me/insights/group/conozco"
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["deleted"] == 2

            # The patrones row survived.
            remaining = await ac.get("/api/v1/users/me/insights")
            assert remaining.status_code == 200
            assert remaining.json()["total"] == 1
            assert remaining.json()["groups"][0]["items"][0]["id"] == str(kept.id)
    finally:
        _clear()


@pytest.mark.asyncio
async def test_delete_all_regression(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        await _seed_insight(
            session,
            user_id,
            insight_type="risk_posture",
            content={
                "type": "risk_posture",
                "posture": "moderate",
                "evidence_basis": "stated",
            },
        )
        await _seed_insight(
            session,
            user_id,
            insight_type="cash_flow_stability",
            content={
                "type": "cash_flow_stability",
                "score_0_100": 70,
                "monthly_variance_pct": "12.5",
                "income_sources_count": 1,
                "savings_rate_pct": "8.0",
            },
            source="computed",
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete("/api/v1/users/me/insights")
            assert resp.status_code == 200, resp.text
            assert resp.json()["deleted"] == 2
    finally:
        _clear()
