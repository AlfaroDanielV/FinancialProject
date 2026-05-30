"""Phase 6f B13 — Goals backend contracts.

Covers:
1. GET /goals returns all goals for the user (no filter).
2. GET /goals?status=active returns only active goals.
3. POST /goals/{id}/contributions adds an aporte and updates current_amount.
4. GET /goals/{id}/contributions returns history sorted most-recent first.
5. GET /goals/{id}/forecast returns has_enough_data=False when no recent contributions.
6. PATCH /goals/{id} updates name and priority.
7. PATCH /goals/{id} status=paused pauses the goal.
8. PATCH /goals/{id} status=active resumes a paused goal.
9. DELETE /goals/{id} marks the goal as abandoned.
10. Contribution that meets target auto-marks goal as achieved.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from api.database import get_db
from api.dependencies import current_user
from api.main import app


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
    app.dependency_overrides[get_db] = _yield_session


def _clear():
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(get_db, None)


async def _create_goal(
    ac: AsyncClient,
    *,
    name: str = "Vacaciones Europa",
    target_amount: float = 1_000_000.0,
    current_amount: float = 0.0,
    target_date: str | None = None,
    monthly_contribution: float | None = 50_000.0,
    priority: int = 3,
) -> dict:
    payload: dict = {
        "name": name,
        "target_amount": target_amount,
        "target_currency": "CRC",
        "current_amount": current_amount,
        "priority": priority,
    }
    if target_date:
        payload["target_date"] = target_date
    if monthly_contribution is not None:
        payload["monthly_contribution"] = monthly_contribution
    resp = await ac.post("/api/v1/goals", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── 1. GET /goals returns all goals ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_goals_returns_all(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await _create_goal(ac, name="Meta A")
            await _create_goal(ac, name="Meta B")

            resp = await ac.get("/api/v1/goals")
            assert resp.status_code == 200, resp.text
            names = [g["name"] for g in resp.json()]
            assert "Meta A" in names
            assert "Meta B" in names
    finally:
        _clear()


# ── 2. GET /goals?status=active filters ───────────────────────────────────────


@pytest.mark.asyncio
async def test_list_goals_status_filter(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            active = await _create_goal(ac, name="Meta activa")
            paused = await _create_goal(ac, name="Meta pausada")
            await ac.patch(
                f"/api/v1/goals/{paused['id']}", json={"status": "paused"}
            )

            resp = await ac.get("/api/v1/goals", params={"status": "active"})
            assert resp.status_code == 200, resp.text
            names = [g["name"] for g in resp.json()]
            assert "Meta activa" in names
            assert "Meta pausada" not in names
    finally:
        _clear()


# ── 3. POST contributions updates current_amount ──────────────────────────────


@pytest.mark.asyncio
async def test_add_contribution_updates_amount(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(ac, name="Meta aporte", current_amount=0.0)

            resp = await ac.post(
                f"/api/v1/goals/{goal['id']}/contributions",
                json={"amount": 100_000.0},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert float(body["goal"]["current_amount"]) == 100_000.0
            assert float(body["contribution"]["amount"]) == 100_000.0
    finally:
        _clear()


# ── 4. GET contributions sorted most-recent first ─────────────────────────────


@pytest.mark.asyncio
async def test_contributions_sorted_desc(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(ac, name="Meta historial")

            await ac.post(
                f"/api/v1/goals/{goal['id']}/contributions",
                json={"amount": 50_000.0},
            )
            await ac.post(
                f"/api/v1/goals/{goal['id']}/contributions",
                json={"amount": 75_000.0},
            )

            resp = await ac.get(f"/api/v1/goals/{goal['id']}/contributions")
            assert resp.status_code == 200, resp.text
            items = resp.json()
            assert len(items) == 2
            # most recent first (higher amount was added last)
            assert float(items[0]["amount"]) == 75_000.0
    finally:
        _clear()


# ── 5. GET forecast returns has_enough_data=False with no recent contribs ─────


@pytest.mark.asyncio
async def test_forecast_no_data(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(ac, name="Meta sin datos")

            resp = await ac.get(f"/api/v1/goals/{goal['id']}/forecast")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["has_enough_data"] is False
            assert body["months_to_target"] is None
    finally:
        _clear()


# ── 6. PATCH updates name and priority ────────────────────────────────────────


@pytest.mark.asyncio
async def test_patch_goal_name_and_priority(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(ac, name="Meta vieja", priority=3)

            resp = await ac.patch(
                f"/api/v1/goals/{goal['id']}",
                json={"name": "Meta actualizada", "priority": 1},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["name"] == "Meta actualizada"
            assert body["priority"] == 1
    finally:
        _clear()


# ── 7. PATCH status=paused ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_patch_goal_pause(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(ac, name="Meta pausable")

            resp = await ac.patch(
                f"/api/v1/goals/{goal['id']}", json={"status": "paused"}
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "paused"
    finally:
        _clear()


# ── 8. PATCH status=active resumes ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_patch_goal_resume(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(ac, name="Meta a reanudar")
            await ac.patch(
                f"/api/v1/goals/{goal['id']}", json={"status": "paused"}
            )

            resp = await ac.patch(
                f"/api/v1/goals/{goal['id']}", json={"status": "active"}
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "active"
    finally:
        _clear()


# ── 9. DELETE marks goal as abandoned ────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_goal_abandons(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(ac, name="Meta a abandonar")

            resp = await ac.delete(f"/api/v1/goals/{goal['id']}")
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "abandoned"
    finally:
        _clear()


# ── 10. Contribution that meets target auto-achieves ─────────────────────────


@pytest.mark.asyncio
async def test_contribution_meeting_target_achieves_goal(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(
                ac, name="Meta completar", target_amount=200_000.0, current_amount=0.0
            )

            resp = await ac.post(
                f"/api/v1/goals/{goal['id']}/contributions",
                json={"amount": 200_000.0},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["goal"]["status"] == "achieved"
    finally:
        _clear()
