"""Phase 6d B2 — onboarding endpoints (accounts ext, recurring_incomes,
onboarding/status, categories).

Endpoint tests use FastAPI's dependency_overrides to inject a stub
`current_user` (matching the in-test user) plus the live `get_db`
session from `db_with_user` so writes hit real Postgres and CHECK
constraints fire.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api.database import get_db
from api.dependencies import current_user
from api.main import app
from api.models.account import Account
from api.models.recurring_bill import RecurringBill
from api.models.recurring_income import RecurringIncome


def _override(session, user_id):
    class _StubUser:
        def __init__(self) -> None:
            self.id = user_id
            self.status = "active"

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user] = lambda: _StubUser()
    app.dependency_overrides[get_db] = _yield_session


def _clear():
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(get_db, None)


# ── POST /accounts: currency + initial_balance ───────────────────────────────


@pytest.mark.asyncio
async def test_create_account_with_explicit_currency_and_balance(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/accounts",
                json={
                    "name": "USD savings",
                    "account_type": "savings",
                    "currency": "USD",
                    "initial_balance": "1500.50",
                },
            )
    finally:
        _clear()
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["currency"] == "USD"
    assert Decimal(body["initial_balance"]) == Decimal("1500.50")


@pytest.mark.asyncio
async def test_create_account_defaults_to_crc_and_zero(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/accounts",
                json={"name": "BAC corriente", "account_type": "checking"},
            )
    finally:
        _clear()
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["currency"] == "CRC"
    assert Decimal(body["initial_balance"]) == Decimal("0.00")


@pytest.mark.asyncio
async def test_create_account_invalid_currency_rejected(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/accounts",
                json={
                    "name": "EUR account",
                    "account_type": "checking",
                    "currency": "EUR",
                },
            )
    finally:
        _clear()
    assert resp.status_code == 422


# ── POST /recurring-incomes ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_salary_round_trip(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/recurring-incomes",
                json={
                    "name": "Salario base",
                    "income_type": "salary",
                    "amount": "850000",
                    "frequency": "monthly",
                    "next_payment_date": str(date.today()),
                },
            )
    finally:
        _clear()
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["income_type"] == "salary"
    assert Decimal(body["amount"]) == Decimal("850000")


@pytest.mark.asyncio
async def test_create_aguinaldo_without_link_rejected_at_pydantic(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/recurring-incomes",
                json={
                    "name": "Aguinaldo huérfano",
                    "income_type": "aguinaldo",
                    "frequency": "annual",
                    "next_payment_date": "2026-12-15",
                },
            )
    finally:
        _clear()
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_aguinaldo_with_unknown_link_404(db_with_user):
    import uuid

    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/recurring-incomes",
                json={
                    "name": "Aguinaldo",
                    "income_type": "aguinaldo",
                    "frequency": "annual",
                    "next_payment_date": "2026-12-15",
                    "base_salary_link_id": str(uuid.uuid4()),
                },
            )
    finally:
        _clear()
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_aguinaldo_derives_amount_from_base_salary(db_with_user):
    session, user_id = db_with_user

    salary = RecurringIncome(
        user_id=user_id,
        name="Salario base",
        income_type="salary",
        amount=Decimal("850000"),
        currency="CRC",
        frequency="monthly",
        next_payment_date=date.today(),
    )
    session.add(salary)
    await session.commit()
    await session.refresh(salary)

    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/recurring-incomes",
                json={
                    "name": "Aguinaldo",
                    "income_type": "aguinaldo",
                    "frequency": "annual",
                    "next_payment_date": "2026-12-15",
                    "base_salary_link_id": str(salary.id),
                },
            )
    finally:
        _clear()
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Aguinaldo derives to monthly salary (850k); user payload had no `amount`.
    assert Decimal(body["amount"]) == Decimal("850000")
    assert body["base_salary_link_id"] == str(salary.id)


@pytest.mark.asyncio
async def test_create_salario_escolar_derives_from_base_salary(db_with_user):
    session, user_id = db_with_user

    salary = RecurringIncome(
        user_id=user_id,
        name="Salario base",
        income_type="salary",
        amount=Decimal("1000000"),
        currency="CRC",
        frequency="monthly",
        next_payment_date=date.today(),
    )
    session.add(salary)
    await session.commit()
    await session.refresh(salary)

    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/recurring-incomes",
                json={
                    "name": "Salario escolar",
                    "income_type": "salario_escolar",
                    "frequency": "annual",
                    "next_payment_date": "2027-01-15",
                    "base_salary_link_id": str(salary.id),
                    # Explicit amount in payload is ignored; server derives.
                    "amount": "1",
                },
            )
    finally:
        _clear()
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert Decimal(body["amount"]) == Decimal("1000000")


# ── GET /onboarding/status ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_onboarding_status_empty(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/onboarding/status")
    finally:
        _clear()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_accounts"] is False
    assert body["has_incomes"] is False
    assert body["has_debts"] is False
    assert body["has_recurring_bills"] is False
    assert body["completeness_score"] == 0.0


@pytest.mark.asyncio
async def test_onboarding_status_partial_after_account_only(db_with_user):
    session, user_id = db_with_user
    session.add(
        Account(user_id=user_id, name="BAC", account_type="checking")
    )
    await session.commit()
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/onboarding/status")
    finally:
        _clear()
    body = resp.json()
    assert body["has_accounts"] is True
    assert body["has_incomes"] is False
    assert body["completeness_score"] == 0.25


@pytest.mark.asyncio
async def test_onboarding_status_complete_with_all_four(db_with_user):
    session, user_id = db_with_user

    session.add(
        Account(user_id=user_id, name="BAC", account_type="checking")
    )
    session.add(
        RecurringIncome(
            user_id=user_id,
            name="Salario",
            income_type="salary",
            amount=Decimal("850000"),
            frequency="monthly",
            next_payment_date=date.today(),
        )
    )
    session.add(
        RecurringBill(
            user_id=user_id,
            name="ICE",
            category="utilities",
            amount_expected=Decimal("18000"),
            frequency="monthly",
            start_date=date.today(),
        )
    )
    # debts requires more fields; insert via raw SQL is overkill — use ORM.
    from api.models.debt import Debt

    session.add(
        Debt(
            user_id=user_id,
            name="Hipoteca",
            debt_type="mortgage",
            original_amount=Decimal("50000000"),
            current_balance=Decimal("48000000"),
            interest_rate=Decimal("0.0850"),
            minimum_payment=Decimal("420000"),
            payment_due_day=15,
        )
    )
    await session.commit()

    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/onboarding/status")
    finally:
        _clear()
    body = resp.json()
    assert body["has_accounts"] is True
    assert body["has_incomes"] is True
    assert body["has_debts"] is True
    assert body["has_recurring_bills"] is True
    assert body["completeness_score"] == 1.0


# ── GET /categories ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_categories_returns_short_cr_list(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/categories")
    finally:
        _clear()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["categories"]) == 10
    assert "alimentación" in body["categories"]
    assert "ingreso_salario" in body["categories"]
    assert "deudas" in body["categories"]


# ── derivation service unit test ────────────────────────────────────────────


def test_derive_amount_for_aguinaldo_equals_monthly_salary():
    from api.services.finance.incomes import derive_amount_for

    assert derive_amount_for("aguinaldo", Decimal("750000")) == Decimal("750000")


def test_derive_amount_for_salario_escolar_equals_monthly_salary():
    from api.services.finance.incomes import derive_amount_for

    assert derive_amount_for("salario_escolar", Decimal("750000")) == Decimal("750000")


def test_derive_amount_for_unknown_type_raises():
    from api.services.finance.incomes import derive_amount_for

    with pytest.raises(ValueError):
        derive_amount_for("bonus", Decimal("100000"))
