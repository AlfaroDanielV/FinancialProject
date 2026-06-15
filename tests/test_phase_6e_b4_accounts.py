"""Phase 6e B4 — Accounts module full backend contracts.

Covers:
- AccountResponse exposes current_balance + month_start_balance.
- AccountUpdate rejects currency/initial_balance and accepts archived.
- Account archive then restore.
- GET /transactions filters: account_id, kind, date range, search.
- PATCH /transactions/{id} happy path.
- PATCH /transactions/{id} rejects shadow rows (Phase 6b) with 409.
- PATCH /transactions/{id} rejects transfer legs with 409.
"""
from __future__ import annotations

from datetime import date, timedelta
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


async def _create_account(ac: AsyncClient, name: str, *, initial: str = "0"):
    response = await ac.post(
        "/api/v1/accounts",
        json={
            "name": name,
            "account_type": "checking",
            "currency": "CRC",
            "initial_balance": initial,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_account_response_includes_current_and_month_start_balance(
    db_with_user,
):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac, "BAC", initial="100000")
            account_id = account["id"]

            today = date.today()
            last_month = (today.replace(day=1) - timedelta(days=1)).isoformat()

            # Pre-month movement: counts toward both month_start and current.
            r1 = await ac.post(
                "/api/v1/transactions",
                json={
                    "amount": "-5000",
                    "currency": "CRC",
                    "merchant": "Tienda",
                    "transaction_date": last_month,
                    "source": "manual",
                    "account_id": account_id,
                },
            )
            assert r1.status_code == 201, r1.text

            # In-month movement: counts only toward current.
            r2 = await ac.post(
                "/api/v1/transactions",
                json={
                    "amount": "20000",
                    "currency": "CRC",
                    "merchant": "Sueldo",
                    "transaction_date": today.isoformat(),
                    "source": "manual",
                    "account_id": account_id,
                },
            )
            assert r2.status_code == 201, r2.text

            listed = await ac.get("/api/v1/accounts")
            assert listed.status_code == 200, listed.text
            row = next(item for item in listed.json() if item["id"] == account_id)
            assert Decimal(str(row["current_balance"])) == Decimal("115000")
            assert Decimal(str(row["month_start_balance"])) == Decimal("95000")

            detail = await ac.get(f"/api/v1/accounts/{account_id}")
            assert detail.status_code == 200, detail.text
            assert Decimal(str(detail.json()["current_balance"])) == Decimal(
                "115000"
            )
            assert Decimal(
                str(detail.json()["month_start_balance"])
            ) == Decimal("95000")
    finally:
        _clear()


@pytest.mark.asyncio
async def test_account_update_rejects_immutable_fields_and_archive_round_trip(
    db_with_user,
):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac, "Davivienda", initial="0")
            account_id = account["id"]

            patched = await ac.patch(
                f"/api/v1/accounts/{account_id}",
                json={"currency": "USD"},
            )
            # AccountUpdate drops currency entirely — body is ignored, not 400.
            assert patched.status_code == 200, patched.text
            assert patched.json()["currency"] == "CRC"

            patched_initial = await ac.patch(
                f"/api/v1/accounts/{account_id}",
                json={"initial_balance": "9999"},
            )
            assert patched_initial.status_code == 200, patched_initial.text
            assert Decimal(str(patched_initial.json()["initial_balance"])) == Decimal(
                "0"
            )

            renamed = await ac.patch(
                f"/api/v1/accounts/{account_id}",
                json={"name": "Davivienda Ahorros", "account_type": "savings"},
            )
            assert renamed.status_code == 200, renamed.text
            assert renamed.json()["name"] == "Davivienda Ahorros"
            assert renamed.json()["account_type"] == "savings"

            archived = await ac.patch(
                f"/api/v1/accounts/{account_id}",
                json={"archived": True, "is_active": False},
            )
            assert archived.status_code == 200, archived.text
            assert archived.json()["archived"] is True

            visible = await ac.get("/api/v1/accounts")
            assert account_id not in {row["id"] for row in visible.json()}

            with_archived = await ac.get(
                "/api/v1/accounts", params={"include_inactive": "true"}
            )
            assert account_id in {row["id"] for row in with_archived.json()}

            restored = await ac.patch(
                f"/api/v1/accounts/{account_id}",
                json={"archived": False, "is_active": True},
            )
            assert restored.status_code == 200, restored.text
            assert restored.json()["archived"] is False

            visible_again = await ac.get("/api/v1/accounts")
            assert account_id in {row["id"] for row in visible_again.json()}
    finally:
        _clear()


@pytest.mark.asyncio
async def test_transactions_list_filters_by_account_kind_dates_and_search(
    db_with_user,
):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            primary = await _create_account(ac, "BAC", initial="0")
            other = await _create_account(ac, "Promerica", initial="0")
            primary_id = primary["id"]
            other_id = other["id"]

            today = date.today()
            old = (today - timedelta(days=60)).isoformat()

            payloads = [
                # primary income today
                {
                    "amount": "10000",
                    "merchant": "Sueldo",
                    "transaction_date": today.isoformat(),
                    "account_id": primary_id,
                    "description": "pago de quincena",
                },
                # primary expense today
                {
                    "amount": "-3000",
                    "merchant": "Auto Mercado",
                    "transaction_date": today.isoformat(),
                    "account_id": primary_id,
                    "description": "super",
                },
                # primary expense 60 days ago
                {
                    "amount": "-500",
                    "merchant": "Café",
                    "transaction_date": old,
                    "account_id": primary_id,
                    "description": "café viejo",
                },
                # other account expense today
                {
                    "amount": "-2000",
                    "merchant": "Otra",
                    "transaction_date": today.isoformat(),
                    "account_id": other_id,
                    "description": "fuera de cuenta",
                },
            ]
            for body in payloads:
                resp = await ac.post(
                    "/api/v1/transactions",
                    json={
                        "currency": "CRC",
                        "source": "manual",
                        **body,
                    },
                )
                assert resp.status_code == 201, resp.text

            # account scope
            scoped = await ac.get(
                "/api/v1/transactions",
                params={"account_id": primary_id},
            )
            assert scoped.status_code == 200, scoped.text
            assert scoped.json()["total"] == 3

            # kind=income
            income = await ac.get(
                "/api/v1/transactions",
                params={"account_id": primary_id, "kind": "income"},
            )
            assert income.json()["total"] == 1
            assert income.json()["items"][0]["amount"] > 0

            # kind=expense
            expense = await ac.get(
                "/api/v1/transactions",
                params={"account_id": primary_id, "kind": "expense"},
            )
            assert expense.json()["total"] == 2

            # date range (last 7 days only)
            recent = await ac.get(
                "/api/v1/transactions",
                params={
                    "account_id": primary_id,
                    "from_date": (today - timedelta(days=7)).isoformat(),
                    "to_date": today.isoformat(),
                },
            )
            assert recent.json()["total"] == 2

            # description search
            found = await ac.get(
                "/api/v1/transactions",
                params={"account_id": primary_id, "q": "super"},
            )
            assert found.json()["total"] == 1
            assert found.json()["items"][0]["merchant"] == "Auto Mercado"
    finally:
        _clear()


@pytest.mark.asyncio
async def test_patch_transaction_updates_confirmed_row(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            account = await _create_account(ac, "BAC", initial="0")
            account_id = account["id"]

            category = await ac.post(
                "/api/v1/categories",
                json={
                    "name": "Restaurantes",
                    "kind": "expense",
                    "color": "#abcdef",
                },
            )
            assert category.status_code == 201, category.text
            category_id = category.json()["id"]

            created = await ac.post(
                "/api/v1/transactions",
                json={
                    "amount": "-1000",
                    "currency": "CRC",
                    "merchant": "Tienda",
                    "transaction_date": date.today().isoformat(),
                    "source": "manual",
                    "account_id": account_id,
                },
            )
            assert created.status_code == 201, created.text
            txn_id = created.json()["id"]

            patched = await ac.patch(
                f"/api/v1/transactions/{txn_id}",
                json={
                    "amount": "-1500",
                    "description": "almuerzo del lunes",
                    "category_id": category_id,
                },
            )
            assert patched.status_code == 200, patched.text
            body = patched.json()
            assert Decimal(str(body["amount"])) == Decimal("-1500")
            assert body["description"] == "almuerzo del lunes"
            assert body["category_id"] == category_id
    finally:
        _clear()


@pytest.mark.asyncio
async def test_patch_transaction_rejects_shadow_and_transfer_legs(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        from sqlalchemy import select as sa_select

        from api.models.transaction import Transaction

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Fund the source so the transfer below clears the funds guard
            # (the -500 row is a shadow, excluded from the balance).
            primary = await _create_account(ac, "BAC", initial="2000")
            other = await _create_account(ac, "Promerica", initial="0")

            # shadow row inserted directly (mimics Gmail 7-day window)
            shadow_resp = await ac.post(
                "/api/v1/transactions",
                json={
                    "amount": "-500",
                    "currency": "CRC",
                    "merchant": "Tienda",
                    "transaction_date": date.today().isoformat(),
                    "source": "manual",
                    "account_id": primary["id"],
                },
            )
            assert shadow_resp.status_code == 201, shadow_resp.text
            shadow_id = shadow_resp.json()["id"]
            shadow_row = (
                await session.execute(
                    sa_select(Transaction).where(Transaction.id == shadow_id)
                )
            ).scalar_one()
            shadow_row.status = "shadow"
            await session.commit()

            blocked = await ac.patch(
                f"/api/v1/transactions/{shadow_id}",
                json={"description": "no"},
            )
            assert blocked.status_code == 409, blocked.text

            transfer = await ac.post(
                "/api/v1/transfers",
                json={
                    "from_account_id": primary["id"],
                    "to_account_id": other["id"],
                    "amount": "1000",
                    "currency": "CRC",
                    "occurred_at": (
                        date.today().isoformat() + "T12:00:00+00:00"
                    ),
                },
            )
            assert transfer.status_code == 201, transfer.text
            leg_id = transfer.json()["debit_transaction_id"]

            transfer_blocked = await ac.patch(
                f"/api/v1/transactions/{leg_id}",
                json={"description": "tampoco"},
            )
            assert transfer_blocked.status_code == 409, transfer_blocked.text
    finally:
        _clear()
