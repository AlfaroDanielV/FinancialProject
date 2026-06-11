"""Phase 7b B1 — GET /transfers account filter + offset for the native UI."""
from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from api.database import get_db
from api.dependencies import current_user
from api.main import app
from api.models.account import Account


def _override(session, user_id):
    class _StubUser:
        def __init__(self) -> None:
            self.id = user_id
            self.status = "active"
            self.currency = "CRC"
            self.display_currency = "CRC"
            self.timezone = "America/Costa_Rica"

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user] = lambda: _StubUser()
    app.dependency_overrides[get_db] = _yield_session


def _clear():
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(get_db, None)


async def _add_account(session, user_id, name: str) -> Account:
    account = Account(
        user_id=user_id,
        name=name,
        account_type="checking",
        currency="CRC",
        initial_balance=Decimal("0"),
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


@pytest.mark.asyncio
async def test_list_transfers_filters_by_account_and_paginates(db_with_user):
    session, user_id = db_with_user
    a = await _add_account(session, user_id, "Cuenta A")
    b = await _add_account(session, user_id, "Cuenta B")
    c = await _add_account(session, user_id, "Cuenta C")

    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            for from_acc, to_acc, amount in (
                (a, b, "1000"),
                (b, c, "2000"),
                (a, c, "3000"),
            ):
                resp = await ac.post(
                    "/api/v1/transfers",
                    json={
                        "from_account_id": str(from_acc.id),
                        "to_account_id": str(to_acc.id),
                        "amount": amount,
                        "currency": "CRC",
                    },
                )
                assert resp.status_code == 201, resp.text

            everything = await ac.get("/api/v1/transfers")
            assert everything.status_code == 200
            assert len(everything.json()) == 3

            only_a = await ac.get(
                "/api/v1/transfers", params={"account_id": str(a.id)}
            )
            assert only_a.status_code == 200
            amounts = sorted(Decimal(t["amount"]) for t in only_a.json())
            assert amounts == [Decimal("1000"), Decimal("3000")]

            only_b = await ac.get(
                "/api/v1/transfers", params={"account_id": str(b.id)}
            )
            amounts = sorted(Decimal(t["amount"]) for t in only_b.json())
            assert amounts == [Decimal("1000"), Decimal("2000")]

            paged = await ac.get(
                "/api/v1/transfers", params={"limit": 2, "offset": 2}
            )
            assert paged.status_code == 200
            assert len(paged.json()) == 1
    finally:
        _clear()
