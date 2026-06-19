"""Phase 6f B12 — Recurring incomes backend contracts.

Covers:
1. GET /recurring-incomes returns active + paused (non-archived) incomes.
2. GET /recurring-incomes?include_archived=true includes archived rows.
3. PATCH /recurring-incomes/{id} updates mutable fields (name, amount).
4. PATCH /recurring-incomes/{id} 422 when attempting to update immutable field (currency).
5. PATCH is_active=false pauses the income; is_active=true resumes it.
6. DELETE /recurring-incomes/{id} archives the income (flips is_active + archived).
7. POST /recurring-incomes/{salary_id}/derive-cycles creates aguinaldo + salario_escolar.
8. POST /recurring-incomes/{salary_id}/derive-cycles is idempotent on second call.
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


async def _create_salary(
    ac: AsyncClient,
    name: str = "Salario CCSS",
    amount: float = 800_000.0,
) -> dict:
    today = date.today()
    resp = await ac.post(
        "/api/v1/recurring-incomes",
        json={
            "name": name,
            "income_type": "salary",
            "amount": amount,
            "currency": "CRC",
            "frequency": "monthly",
            "next_payment_date": today.isoformat(),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── 1. GET returns active + paused (not archived) ─────────────────────────────


@pytest.mark.asyncio
async def test_list_incomes_returns_active_and_paused(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            salary = await _create_salary(ac, "Salario activo")

            # Pause a second income
            paused = await _create_salary(ac, "Salario pausado", amount=600_000.0)
            await ac.patch(
                f"/api/v1/recurring-incomes/{paused['id']}",
                json={"is_active": False},
            )

            resp = await ac.get("/api/v1/recurring-incomes")
            assert resp.status_code == 200, resp.text
            names = [i["name"] for i in resp.json()]
            assert "Salario activo" in names
            assert "Salario pausado" in names
    finally:
        _clear()


# ── 2. include_archived=true returns archived rows ────────────────────────────


@pytest.mark.asyncio
async def test_list_incomes_include_archived(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            salary = await _create_salary(ac, "Salario temporal")
            await ac.delete(f"/api/v1/recurring-incomes/{salary['id']}")

            # Without flag: not visible
            resp = await ac.get("/api/v1/recurring-incomes")
            names = [i["name"] for i in resp.json()]
            assert "Salario temporal" not in names

            # With flag: visible
            resp2 = await ac.get(
                "/api/v1/recurring-incomes", params={"include_archived": True}
            )
            names2 = [i["name"] for i in resp2.json()]
            assert "Salario temporal" in names2
    finally:
        _clear()


# ── 3. PATCH updates mutable fields ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_patch_income_updates_name_and_amount(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            salary = await _create_salary(ac, "Salario viejo", amount=500_000.0)

            resp = await ac.patch(
                f"/api/v1/recurring-incomes/{salary['id']}",
                json={"name": "Salario actualizado", "amount": 750_000.0},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["name"] == "Salario actualizado"
            assert float(body["amount"]) == 750_000.0
    finally:
        _clear()


# ── 4. PATCH rejects immutable field (currency) ────────────────────────────────


@pytest.mark.asyncio
async def test_patch_income_rejects_immutable_currency(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            salary = await _create_salary(ac)

            resp = await ac.patch(
                f"/api/v1/recurring-incomes/{salary['id']}",
                json={"currency": "USD"},
            )
            assert resp.status_code == 422, resp.text
    finally:
        _clear()


# ── 5. Pause and resume via is_active ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_pause_and_resume_income(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            salary = await _create_salary(ac, "Salario pausable")

            pause_resp = await ac.patch(
                f"/api/v1/recurring-incomes/{salary['id']}",
                json={"is_active": False},
            )
            assert pause_resp.status_code == 200, pause_resp.text
            assert pause_resp.json()["is_active"] is False
            assert pause_resp.json()["archived"] is False

            resume_resp = await ac.patch(
                f"/api/v1/recurring-incomes/{salary['id']}",
                json={"is_active": True},
            )
            assert resume_resp.status_code == 200, resume_resp.text
            assert resume_resp.json()["is_active"] is True
    finally:
        _clear()


# ── 6. DELETE archives the income ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_income_archives(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            salary = await _create_salary(ac, "Salario a archivar")

            del_resp = await ac.delete(
                f"/api/v1/recurring-incomes/{salary['id']}"
            )
            assert del_resp.status_code == 200, del_resp.text
            body = del_resp.json()
            assert body["archived"] is True
            assert body["is_active"] is False

            list_resp = await ac.get("/api/v1/recurring-incomes")
            ids = [i["id"] for i in list_resp.json()]
            assert salary["id"] not in ids
    finally:
        _clear()


# ── 7. derive-cycles creates aguinaldo + salario_escolar ─────────────────────


@pytest.mark.asyncio
async def test_derive_cycles_creates_both_rows(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            salary = await _create_salary(ac, "Salario CCSS", amount=1_000_000.0)

            resp = await ac.post(
                f"/api/v1/recurring-incomes/{salary['id']}/derive-cycles"
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert "aguinaldo" in body
            assert "salario_escolar" in body
            assert body["created_aguinaldo"] is True
            assert body["created_salario_escolar"] is True

            # Amounts should be CR-law derived (aguinaldo = 1/12 of annual)
            assert float(body["aguinaldo"]["amount"]) > 0
            assert float(body["salario_escolar"]["amount"]) > 0

            # Both should link back to the salary
            assert body["aguinaldo"]["base_salary_link_id"] == salary["id"]
            assert body["salario_escolar"]["base_salary_link_id"] == salary["id"]
    finally:
        _clear()


# ── 8. derive-cycles is idempotent ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_derive_cycles_idempotent(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            salary = await _create_salary(ac, "Salario idempotente", amount=900_000.0)

            first = await ac.post(
                f"/api/v1/recurring-incomes/{salary['id']}/derive-cycles"
            )
            assert first.status_code == 201, first.text

            second = await ac.post(
                f"/api/v1/recurring-incomes/{salary['id']}/derive-cycles"
            )
            assert second.status_code == 201, second.text
            body = second.json()
            assert body["created_aguinaldo"] is False
            assert body["created_salario_escolar"] is False

            # Same IDs returned on replay
            assert (
                body["aguinaldo"]["id"] == first.json()["aguinaldo"]["id"]
            )
            assert (
                body["salario_escolar"]["id"]
                == first.json()["salario_escolar"]["id"]
            )
    finally:
        _clear()
