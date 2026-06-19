"""Phase 6f B11 — Debts + amortization + payoff-scenarios backend contracts.

Covers:
1. GET /debts returns active debts for the user.
2. GET /debts/overview returns total_debt, total_minimum_monthly, upcoming_payments.
3. GET /debts/{id}/schedule returns AmortizationSchedule with rows.
4. GET /debts/{id}/payoff-scenarios with lump_sum returns savings + Ley 7472 info.
5. GET /debts/{id}/payoff-scenarios 422 when neither param is provided.
6. PATCH /debts/{id} updates name and notes (whitelist-only fields).
7. PATCH /debts/{id} 422 when attempting to update immutable financial field.
8. DELETE /debts/{id} archives the debt (flips is_active + archived).
"""
from __future__ import annotations

from datetime import date

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


async def _create_account(ac: AsyncClient, name: str = "BAC") -> dict:
    resp = await ac.post(
        "/api/v1/accounts",
        json={"name": name, "account_type": "checking", "currency": "CRC", "initial_balance": "0"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_debt(
    ac: AsyncClient,
    account_id: str,
    *,
    name: str = "Préstamo personal",
    current_balance: float = 500_000.0,
    interest_rate: float = 0.18,
    minimum_payment: float = 20_000.0,
    term_months: int = 36,
) -> dict:
    today = date.today()
    resp = await ac.post(
        "/api/v1/debts",
        json={
            "name": name,
            "debt_type": "personal_loan",
            "original_amount": current_balance,
            "current_balance": current_balance,
            "interest_rate": interest_rate,
            "minimum_payment": minimum_payment,
            "payment_due_day": 15,
            "account_id": account_id,
            "term_months": term_months,
            "start_date": today.isoformat(),
            "currency": "CRC",
            "rate_type": "fixed",
            "prepayment_penalty_pct": 0.0,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── 1. GET /debts returns active debts ───────────────────────────────────────


@pytest.mark.asyncio
async def test_list_debts_returns_active(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac)
            await _create_debt(ac, account["id"], name="Hipoteca BAC")

            resp = await ac.get("/api/v1/debts")
            assert resp.status_code == 200, resp.text
            debts = resp.json()
            names = [d["name"] for d in debts]
            assert "Hipoteca BAC" in names
    finally:
        _clear()


# ── 2. GET /debts/overview ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_debt_overview_returns_totals(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac, "Davivienda")
            await _create_debt(ac, account["id"], current_balance=300_000.0, minimum_payment=15_000.0)
            await _create_debt(ac, account["id"], name="Tarjeta Promerica", current_balance=200_000.0, minimum_payment=10_000.0)

            resp = await ac.get("/api/v1/debts/overview")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["total_debt"] >= 500_000
            assert body["total_minimum_monthly"] >= 25_000
            assert "upcoming_payments" in body
    finally:
        _clear()


# ── 3. GET /debts/{id}/schedule returns amortization rows ────────────────────


@pytest.mark.asyncio
async def test_debt_schedule_returns_rows(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac, "Scotiabank")
            debt = await _create_debt(ac, account["id"], term_months=12, current_balance=120_000.0, minimum_payment=12_000.0)

            resp = await ac.get(f"/api/v1/debts/{debt['id']}/schedule")
            assert resp.status_code == 200, resp.text
            sched = resp.json()
            assert "schedule" in sched
            assert len(sched["schedule"]) > 0
            row = sched["schedule"][0]
            assert "month_number" in row
            assert "payment_amount" in row
            assert "remaining_balance" in row
    finally:
        _clear()


# ── 4. GET /debts/{id}/payoff-scenarios with lump_sum ────────────────────────


@pytest.mark.asyncio
async def test_payoff_scenarios_lump_sum(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac, "BCR")
            debt = await _create_debt(
                ac, account["id"],
                current_balance=500_000.0,
                interest_rate=0.15,
                minimum_payment=18_000.0,
                term_months=36,
            )

            resp = await ac.get(
                f"/api/v1/debts/{debt['id']}/payoff-scenarios",
                params={"lump_sum": 100_000},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "original" in body
            assert "lump_sum" in body
            assert body["lump_sum"]["months_saved"] >= 0
            assert "prepayment_penalty_applies" in body["lump_sum"]
    finally:
        _clear()


# ── 5. payoff-scenarios 422 when no params ────────────────────────────────────


@pytest.mark.asyncio
async def test_payoff_scenarios_422_no_params(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac, "INS")
            debt = await _create_debt(ac, account["id"])

            resp = await ac.get(f"/api/v1/debts/{debt['id']}/payoff-scenarios")
            assert resp.status_code == 422, resp.text
    finally:
        _clear()


# ── 6. PATCH updates name and notes ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_patch_debt_updates_name_and_notes(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac)
            debt = await _create_debt(ac, account["id"], name="Préstamo viejo")

            resp = await ac.patch(
                f"/api/v1/debts/{debt['id']}",
                json={"name": "Préstamo refinanciado", "notes": "Refinanciado en junio 2026"},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["name"] == "Préstamo refinanciado"
            assert body["notes"] == "Refinanciado en junio 2026"
    finally:
        _clear()


# ── 7. PATCH rejects immutable financial field ────────────────────────────────


@pytest.mark.asyncio
async def test_patch_debt_rejects_immutable_field(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac)
            debt = await _create_debt(ac, account["id"])

            resp = await ac.patch(
                f"/api/v1/debts/{debt['id']}",
                json={"interest_rate": 0.05},
            )
            assert resp.status_code == 422, resp.text
    finally:
        _clear()


# ── 8. DELETE archives the debt ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_debt_archives(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac, "Coopealianza")
            debt = await _create_debt(ac, account["id"], name="Préstamo personal CR")

            resp = await ac.delete(f"/api/v1/debts/{debt['id']}")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["archived"] is True
            assert body["is_active"] is False

            list_resp = await ac.get("/api/v1/debts")
            ids = [d["id"] for d in list_resp.json()]
            assert debt["id"] not in ids
    finally:
        _clear()
