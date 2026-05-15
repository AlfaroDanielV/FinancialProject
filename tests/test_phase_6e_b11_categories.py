"""Phase 6e B11 — categories management backend invariants.

The CRUD endpoints already shipped in B2; B11 verifies the two invariants
the SPA depends on:

- Renaming a category keeps the same FK on linked transactions (history
  picks up the new name automatically because rows store category_id, not
  the string).
- Default categories cannot be archived (400 from the PATCH endpoint).
"""
from __future__ import annotations

from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select as sa_select

from api.database import get_db
from api.dependencies import current_user
from api.main import app
from api.models.transaction import Transaction
from api.models.user_category import UserCategory


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


@pytest.mark.asyncio
async def test_renaming_a_category_preserves_transaction_link(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            cat = await ac.post(
                "/api/v1/categories",
                json={
                    "name": "Comidas afuera",
                    "kind": "expense",
                    "color": "#ff6600",
                },
            )
            assert cat.status_code == 201, cat.text
            category_id = cat.json()["id"]

            account = await ac.post(
                "/api/v1/accounts",
                json={
                    "name": "BAC",
                    "account_type": "checking",
                    "currency": "CRC",
                    "initial_balance": "0",
                },
            )
            assert account.status_code == 201

            txn = await ac.post(
                "/api/v1/transactions",
                json={
                    "amount": "-7500",
                    "currency": "CRC",
                    "merchant": "Restaurante",
                    "transaction_date": date.today().isoformat(),
                    "source": "manual",
                    "account_id": account.json()["id"],
                    "category_id": category_id,
                },
            )
            assert txn.status_code == 201, txn.text
            txn_id = txn.json()["id"]

            renamed = await ac.patch(
                f"/api/v1/categories/{category_id}",
                json={"name": "Restaurantes"},
            )
            assert renamed.status_code == 200, renamed.text
            assert renamed.json()["name"] == "Restaurantes"
            assert renamed.json()["transaction_count"] == 1

            row = (
                await session.execute(
                    sa_select(Transaction).where(Transaction.id == txn_id)
                )
            ).scalar_one()
            assert str(row.category_id) == category_id
    finally:
        _clear()


@pytest.mark.asyncio
async def test_default_categories_cannot_be_archived(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # `GET /categories` seeds defaults on first call.
            listed = await ac.get("/api/v1/categories")
            assert listed.status_code == 200, listed.text
            defaults = [c for c in listed.json() if c["is_default"]]
            assert defaults, "default categories should have been seeded"

            target = defaults[0]
            blocked = await ac.patch(
                f"/api/v1/categories/{target['id']}",
                json={"archived": True},
            )
            assert blocked.status_code == 400, blocked.text
            assert "default" in blocked.json()["detail"]

            row = (
                await session.execute(
                    sa_select(UserCategory).where(
                        UserCategory.id == target["id"]
                    )
                )
            ).scalar_one()
            assert row.archived is False
    finally:
        _clear()


@pytest.mark.asyncio
async def test_custom_category_archive_and_restore_round_trip(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            created = await ac.post(
                "/api/v1/categories",
                json={
                    "name": "Marchamo",
                    "kind": "expense",
                    "color": "#123456",
                },
            )
            assert created.status_code == 201, created.text
            category_id = created.json()["id"]

            archived = await ac.patch(
                f"/api/v1/categories/{category_id}",
                json={"archived": True},
            )
            assert archived.status_code == 200, archived.text
            assert archived.json()["archived"] is True

            default_list = await ac.get("/api/v1/categories")
            assert category_id not in {c["id"] for c in default_list.json()}

            with_archived = await ac.get(
                "/api/v1/categories", params={"include_archived": "true"}
            )
            assert category_id in {c["id"] for c in with_archived.json()}

            restored = await ac.patch(
                f"/api/v1/categories/{category_id}",
                json={"archived": False},
            )
            assert restored.status_code == 200, restored.text
            assert restored.json()["archived"] is False

            visible = await ac.get("/api/v1/categories")
            assert category_id in {c["id"] for c in visible.json()}
    finally:
        _clear()
