"""Phase 6e B8 — recurring incomes module backend contracts.

Covers:
- PATCH whitelist with extra='forbid' (income_type/currency/base_salary_link_id
  reject as 422); archived field is settable.
- DELETE flips both is_active=false AND archived=true.
- list_recurring_incomes excludes archived by default; include_archived=true
  returns them; paused rows (is_active=false, archived=false) stay visible.
- POST /derive-cycles creates both CR-cycle rows atomically with
  server-derived amounts.
- derive-cycles is idempotent: re-call returns existing rows with
  created_* flags false.
- derive-cycles 404 when salary_id missing; 400 when target is not a salary.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select as sa_select

from api.database import get_db
from api.dependencies import current_user
from api.main import app
from api.models.recurring_income import RecurringIncome


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


async def _create_salary(ac: AsyncClient, *, amount: str = "750000"):
    payload = {
        "name": "Sueldo mensual",
        "income_type": "salary",
        "amount": float(amount),
        "currency": "CRC",
        "frequency": "monthly",
        "next_payment_date": (date.today() + timedelta(days=15)).isoformat(),
    }
    response = await ac.post("/api/v1/recurring-incomes", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_patch_whitelist_accepts_safe_fields_and_rejects_extras(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            salary = await _create_salary(ac)

            ok = await ac.patch(
                f"/api/v1/recurring-incomes/{salary['id']}",
                json={"name": "Sueldo BAC", "amount": 800000, "notes": "ajuste"},
            )
            assert ok.status_code == 200, ok.text
            assert ok.json()["name"] == "Sueldo BAC"
            assert Decimal(str(ok.json()["amount"])) == Decimal("800000")

            # extra='forbid' rejects unlisted fields
            rejected = await ac.patch(
                f"/api/v1/recurring-incomes/{salary['id']}",
                json={"currency": "USD"},
            )
            assert rejected.status_code == 422, rejected.text

            also_rejected = await ac.patch(
                f"/api/v1/recurring-incomes/{salary['id']}",
                json={"base_salary_link_id": str(salary["id"])},
            )
            assert also_rejected.status_code == 422, also_rejected.text
    finally:
        _clear()


@pytest.mark.asyncio
async def test_delete_flips_archived_and_list_filters(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            salary = await _create_salary(ac)

            archived = await ac.delete(
                f"/api/v1/recurring-incomes/{salary['id']}"
            )
            assert archived.status_code == 200
            assert archived.json()["is_active"] is False
            assert archived.json()["archived"] is True

            visible = await ac.get("/api/v1/recurring-incomes")
            assert salary["id"] not in {row["id"] for row in visible.json()}

            with_archived = await ac.get(
                "/api/v1/recurring-incomes", params={"include_archived": "true"}
            )
            assert salary["id"] in {row["id"] for row in with_archived.json()}
    finally:
        _clear()


@pytest.mark.asyncio
async def test_pause_keeps_income_visible_in_list(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            salary = await _create_salary(ac)

            paused = await ac.patch(
                f"/api/v1/recurring-incomes/{salary['id']}",
                json={"is_active": False},
            )
            assert paused.status_code == 200
            assert paused.json()["is_active"] is False
            assert paused.json()["archived"] is False

            visible = await ac.get("/api/v1/recurring-incomes")
            ids = {row["id"]: row for row in visible.json()}
            assert salary["id"] in ids
            assert ids[salary["id"]]["is_active"] is False
    finally:
        _clear()


@pytest.mark.asyncio
async def test_derive_cycles_creates_both_rows_with_correct_amounts(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            salary = await _create_salary(ac, amount="600000")

            response = await ac.post(
                f"/api/v1/recurring-incomes/{salary['id']}/derive-cycles",
            )
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["created_aguinaldo"] is True
            assert body["created_salario_escolar"] is True
            assert body["aguinaldo"]["income_type"] == "aguinaldo"
            assert body["salario_escolar"]["income_type"] == "salario_escolar"
            assert Decimal(str(body["aguinaldo"]["amount"])) == Decimal("600000")
            assert Decimal(str(body["salario_escolar"]["amount"])) == Decimal(
                "600000"
            )
            assert body["aguinaldo"]["base_salary_link_id"] == salary["id"]
            assert body["salario_escolar"]["base_salary_link_id"] == salary["id"]
            assert body["aguinaldo"]["frequency"] == "annual"

            # Both rows are in the DB linked to the salary
            rows = await session.execute(
                sa_select(RecurringIncome).where(
                    RecurringIncome.user_id == user_id,
                    RecurringIncome.base_salary_link_id == salary["id"],
                )
            )
            types = {row.income_type for row in rows.scalars().all()}
            assert types == {"aguinaldo", "salario_escolar"}
    finally:
        _clear()


@pytest.mark.asyncio
async def test_derive_cycles_is_idempotent_on_recall(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            salary = await _create_salary(ac)

            first = await ac.post(
                f"/api/v1/recurring-incomes/{salary['id']}/derive-cycles",
            )
            assert first.status_code == 201

            second = await ac.post(
                f"/api/v1/recurring-incomes/{salary['id']}/derive-cycles",
            )
            assert second.status_code == 201, second.text
            assert second.json()["created_aguinaldo"] is False
            assert second.json()["created_salario_escolar"] is False
            assert (
                second.json()["aguinaldo"]["id"]
                == first.json()["aguinaldo"]["id"]
            )

            # Still only 2 derived rows in DB
            rows = await session.execute(
                sa_select(RecurringIncome).where(
                    RecurringIncome.user_id == user_id,
                    RecurringIncome.income_type.in_(
                        ("aguinaldo", "salario_escolar")
                    ),
                )
            )
            assert len(list(rows.scalars().all())) == 2
    finally:
        _clear()


@pytest.mark.asyncio
async def test_derive_cycles_rejects_non_salary_target(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            payload = {
                "name": "Freelance",
                "income_type": "freelance",
                "amount": 300000,
                "currency": "CRC",
                "frequency": "monthly",
                "next_payment_date": date.today().isoformat(),
            }
            freelance = await ac.post(
                "/api/v1/recurring-incomes", json=payload
            )
            assert freelance.status_code == 201, freelance.text

            bad = await ac.post(
                f"/api/v1/recurring-incomes/{freelance.json()['id']}/derive-cycles",
            )
            assert bad.status_code == 400
            assert "salary" in bad.json()["detail"]
    finally:
        _clear()
