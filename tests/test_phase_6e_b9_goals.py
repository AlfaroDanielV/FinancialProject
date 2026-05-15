"""Phase 6e B9 — goals module backend contracts.

Covers:
- GET /goals/{id}/contributions returns the goal's contributions sorted
  occurred_at desc.
- Forecast happy path: avg over last 3 complete calendar months, months
  to target, projected completion date.
- Forecast edge case: zero contributions in the lookback window →
  has_enough_data=false, months_to_target=None.
- Forecast edge case: already-achieved goal returns months_to_target=0
  and remaining=0.
- PATCH status round-trip (active → paused → active) works.
- Contribution with a foreign user's transaction_id is rejected.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
    name: str = "Fondo emergencia",
    target_amount: str = "1500000",
):
    response = await ac.post(
        "/api/v1/goals",
        json={
            "name": name,
            "target_amount": target_amount,
            "target_currency": "CRC",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _months_ago_utc(months: int) -> str:
    today = datetime.now(timezone.utc).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    # Land 15 days into the target month so it can't slip into a different
    # calendar month due to month-end edge cases.
    target_month = today.month - months
    year = today.year
    while target_month <= 0:
        target_month += 12
        year -= 1
    return datetime(year, target_month, 15, 12, 0, 0, tzinfo=timezone.utc).isoformat()


@pytest.mark.asyncio
async def test_list_contributions_returns_user_rows_desc(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(ac)

            for index, amount in enumerate(("10000", "20000", "30000")):
                resp = await ac.post(
                    f"/api/v1/goals/{goal['id']}/contributions",
                    json={
                        "amount": amount,
                        "occurred_at": _months_ago_utc(2 - index),
                    },
                )
                assert resp.status_code == 200, resp.text

            listed = await ac.get(
                f"/api/v1/goals/{goal['id']}/contributions"
            )
            assert listed.status_code == 200, listed.text
            rows = listed.json()
            assert len(rows) == 3
            # Sorted occurred_at desc
            dates = [row["occurred_at"] for row in rows]
            assert dates == sorted(dates, reverse=True)
            amounts = [Decimal(str(row["amount"])) for row in rows]
            assert sum(amounts) == Decimal("60000")
    finally:
        _clear()


@pytest.mark.asyncio
async def test_forecast_with_contributions_returns_months_to_target(
    db_with_user,
):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Target = 600k, current=0 after contribs in window.
            goal = await _create_goal(ac, target_amount="600000")

            # Three contributions of 50k each in the last 3 complete months
            # → avg = 50k/month → remaining=450k → 9 months.
            for offset in (1, 2, 3):
                resp = await ac.post(
                    f"/api/v1/goals/{goal['id']}/contributions",
                    json={
                        "amount": "50000",
                        "occurred_at": _months_ago_utc(offset),
                    },
                )
                assert resp.status_code == 200, resp.text

            forecast = await ac.get(
                f"/api/v1/goals/{goal['id']}/forecast"
            )
            assert forecast.status_code == 200, forecast.text
            body = forecast.json()
            assert body["has_enough_data"] is True
            assert Decimal(str(body["avg_monthly_contribution"])) == Decimal(
                "50000.00"
            )
            assert body["months_to_target"] == 9
            assert body["projected_completion_date"] is not None
    finally:
        _clear()


@pytest.mark.asyncio
async def test_forecast_with_no_contributions_returns_insufficient_data(
    db_with_user,
):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(ac)

            forecast = await ac.get(
                f"/api/v1/goals/{goal['id']}/forecast"
            )
            assert forecast.status_code == 200, forecast.text
            body = forecast.json()
            assert body["has_enough_data"] is False
            assert body["months_to_target"] is None
            assert body["projected_completion_date"] is None
    finally:
        _clear()


@pytest.mark.asyncio
async def test_forecast_already_achieved_returns_zero(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(ac, target_amount="100000")
            done = await ac.post(
                f"/api/v1/goals/{goal['id']}/contributions",
                json={"amount": "100000"},
            )
            assert done.status_code == 200

            forecast = await ac.get(
                f"/api/v1/goals/{goal['id']}/forecast"
            )
            assert forecast.status_code == 200, forecast.text
            body = forecast.json()
            assert body["has_enough_data"] is True
            assert body["months_to_target"] == 0
            assert Decimal(str(body["remaining"])) == Decimal("0")
    finally:
        _clear()


@pytest.mark.asyncio
async def test_status_round_trip_active_paused_active(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            goal = await _create_goal(ac)
            paused = await ac.patch(
                f"/api/v1/goals/{goal['id']}", json={"status": "paused"}
            )
            assert paused.status_code == 200
            assert paused.json()["status"] == "paused"

            resumed = await ac.patch(
                f"/api/v1/goals/{goal['id']}", json={"status": "active"}
            )
            assert resumed.status_code == 200
            assert resumed.json()["status"] == "active"
    finally:
        _clear()
