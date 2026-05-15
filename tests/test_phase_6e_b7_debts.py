"""Phase 6e B7 — debts module backend contracts.

Covers:
- PATCH /debts/{id} accepts the narrow whitelist (name, payment_due_day,
  account_id, notes, is_active, archived) and rejects financial fields
  via Pydantic (422 Unprocessable Entity).
- DELETE flips both is_active=false AND archived=true.
- list_debts excludes archived by default; include_archived=true returns.
- debt_overview excludes archived rows.
- GET /payoff-scenarios?lump_sum=… returns expected savings + payoff date.
- GET /payoff-scenarios?extra_monthly=… returns expected savings.
- GET /payoff-scenarios with both params returns both scenarios.
- GET /payoff-scenarios with neither returns 422.
- Ley 7472: prepayment_penalty_amount is 0 when payments_made >= 2.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select as sa_select

from api.database import get_db
from api.dependencies import current_user
from api.main import app
from api.models.debt import Debt


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


async def _create_debt(
    ac: AsyncClient,
    *,
    name: str = "BAC Personal",
    payments_made: int = 0,
    prepayment_penalty_pct: float = 0.0,
):
    body = {
        "name": name,
        "debt_type": "personal_loan",
        "lender": "BAC",
        "original_amount": 1_000_000,
        "current_balance": 1_000_000,
        "interest_rate": 0.18,
        "minimum_payment": 25_000,
        "payment_due_day": 5,
        "term_months": 60,
        "start_date": date.today().isoformat(),
        "currency": "CRC",
        "rate_type": "fixed",
        "prepayment_penalty_pct": prepayment_penalty_pct,
        "payments_made": payments_made,
        "includes_insurance": False,
    }
    response = await ac.post("/api/v1/debts", json=body)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_patch_whitelist_accepts_safe_fields_and_rejects_financials(
    db_with_user,
):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            debt = await _create_debt(ac)
            debt_id = debt["id"]

            ok = await ac.patch(
                f"/api/v1/debts/{debt_id}",
                json={
                    "name": "BAC Personal (renombrado)",
                    "payment_due_day": 15,
                    "notes": "renegociado",
                },
            )
            assert ok.status_code == 200, ok.text
            assert ok.json()["name"] == "BAC Personal (renombrado)"
            assert ok.json()["payment_due_day"] == 15

            # Pydantic rejects extra fields (`current_balance` not on schema)
            rejected = await ac.patch(
                f"/api/v1/debts/{debt_id}",
                json={"current_balance": 1},
            )
            assert rejected.status_code == 422, rejected.text

            # Confirm the financial field stayed at its created value.
            row = (
                await session.execute(
                    sa_select(Debt).where(Debt.id == debt_id)
                )
            ).scalar_one()
            assert Decimal(str(row.current_balance)) == Decimal("1000000.00")
    finally:
        _clear()


@pytest.mark.asyncio
async def test_delete_flips_archived_and_list_filters_default(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            debt = await _create_debt(ac)
            debt_id = debt["id"]

            archived = await ac.delete(f"/api/v1/debts/{debt_id}")
            assert archived.status_code == 200, archived.text
            assert archived.json()["is_active"] is False
            assert archived.json()["archived"] is True

            visible = await ac.get("/api/v1/debts")
            assert debt_id not in {row["id"] for row in visible.json()}

            with_archived = await ac.get(
                "/api/v1/debts", params={"include_archived": "true"}
            )
            assert debt_id in {row["id"] for row in with_archived.json()}

            overview = await ac.get("/api/v1/debts/overview")
            assert overview.status_code == 200
            assert overview.json()["total_debt"] == 0
    finally:
        _clear()


@pytest.mark.asyncio
async def test_pause_keeps_visible_archive_removes_from_default_list(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            debt = await _create_debt(ac)
            debt_id = debt["id"]

            paused = await ac.patch(
                f"/api/v1/debts/{debt_id}",
                json={"is_active": False},
            )
            assert paused.status_code == 200
            assert paused.json()["is_active"] is False
            assert paused.json()["archived"] is False

            # Paused stays visible by default (archived=false), even though
            # it's excluded from the overview totals.
            visible = await ac.get("/api/v1/debts")
            assert debt_id in {row["id"] for row in visible.json()}

            overview = await ac.get("/api/v1/debts/overview")
            assert overview.json()["total_debt"] == 0

            # Resume
            resumed = await ac.patch(
                f"/api/v1/debts/{debt_id}",
                json={"is_active": True},
            )
            assert resumed.status_code == 200
            assert resumed.json()["is_active"] is True

            overview_after = await ac.get("/api/v1/debts/overview")
            assert overview_after.json()["total_debt"] > 0
    finally:
        _clear()


@pytest.mark.asyncio
async def test_payoff_scenarios_lump_sum_only(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            debt = await _create_debt(ac)

            response = await ac.get(
                f"/api/v1/debts/{debt['id']}/payoff-scenarios",
                params={"lump_sum": 200_000},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["lump_sum"] is not None
            assert body["extra_monthly"] is None
            assert body["lump_sum"]["months_saved"] > 0
            assert body["lump_sum"]["interest_saved"] > 0
            assert body["lump_sum"]["new_total_months"] < body["original"]["total_months"]
    finally:
        _clear()


@pytest.mark.asyncio
async def test_payoff_scenarios_extra_monthly_only(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            debt = await _create_debt(ac)

            response = await ac.get(
                f"/api/v1/debts/{debt['id']}/payoff-scenarios",
                params={"extra_monthly": 10_000},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["extra_monthly"] is not None
            assert body["lump_sum"] is None
            assert body["extra_monthly"]["months_saved"] > 0
            assert body["extra_monthly"]["interest_saved"] > 0
    finally:
        _clear()


@pytest.mark.asyncio
async def test_payoff_scenarios_both_params_returns_both(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            debt = await _create_debt(ac)

            response = await ac.get(
                f"/api/v1/debts/{debt['id']}/payoff-scenarios",
                params={"lump_sum": 200_000, "extra_monthly": 10_000},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["lump_sum"] is not None
            assert body["extra_monthly"] is not None
    finally:
        _clear()


@pytest.mark.asyncio
async def test_payoff_scenarios_requires_at_least_one_param(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            debt = await _create_debt(ac)

            response = await ac.get(
                f"/api/v1/debts/{debt['id']}/payoff-scenarios"
            )
            assert response.status_code == 422, response.text
    finally:
        _clear()


@pytest.mark.asyncio
async def test_ley_7472_penalty_zero_after_two_payments(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Penalty applies: payments_made < 2, prepayment_penalty_pct > 0
            young_debt = await _create_debt(
                ac,
                name="Joven",
                payments_made=0,
                prepayment_penalty_pct=0.02,
            )
            young = await ac.get(
                f"/api/v1/debts/{young_debt['id']}/payoff-scenarios",
                params={"lump_sum": 200_000},
            )
            assert young.status_code == 200, young.text
            assert young.json()["lump_sum"]["prepayment_penalty_applies"] is True
            assert young.json()["lump_sum"]["prepayment_penalty_amount"] > 0

            # No penalty: payments_made >= 2 (Ley 7472)
            seasoned = await _create_debt(
                ac,
                name="Madura",
                payments_made=3,
                prepayment_penalty_pct=0.02,
            )
            mature = await ac.get(
                f"/api/v1/debts/{seasoned['id']}/payoff-scenarios",
                params={"lump_sum": 200_000},
            )
            assert mature.status_code == 200, mature.text
            assert mature.json()["lump_sum"]["prepayment_penalty_applies"] is False
            assert mature.json()["lump_sum"]["prepayment_penalty_amount"] == 0
    finally:
        _clear()
