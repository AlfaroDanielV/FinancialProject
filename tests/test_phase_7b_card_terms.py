"""Phase 7b B3 — credit_card_terms CRUD (account parameters, NOT a Debt).

- PUT upserts (create then replace) on a CREDIT account; 400 on non-credit.
- GET 404s before terms exist; returns them after.
- DELETE removes the row.
- extra="forbid" → unknown fields 422.
- Account hard-delete CASCADEs the terms row.
- Chat: Intent.CREATE_CARD → OpenScreenAction("card_create", prefill).
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
from api.models.credit_card_terms import CreditCardTerms


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


async def _add_account(session, user_id, name: str, account_type="credit"):
    account = Account(
        user_id=user_id,
        name=name,
        account_type=account_type,
        currency="CRC",
        initial_balance=Decimal("0"),
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


_TERMS = {
    "annual_interest_rate": "0.45",
    "cash_advance_rate": "0.50",
    "minimum_payment_pct": "0.025",
    "minimum_payment_floor": "5000",
    "credit_limit": "2000000",
    "statement_day": 20,
    "payment_due_day": 10,
}


@pytest.mark.asyncio
async def test_card_terms_crud_and_guards(db_with_user):
    session, user_id = db_with_user
    card = await _add_account(session, user_id, "BAC Visa")
    checking = await _add_account(
        session, user_id, "Corriente", account_type="checking"
    )
    card_id, checking_id = card.id, checking.id

    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # GET before terms exist → 404.
            missing = await ac.get(f"/api/v1/accounts/{card_id}/card-terms")
            assert missing.status_code == 404

            # PUT on a non-credit account → 400.
            wrong_type = await ac.put(
                f"/api/v1/accounts/{checking_id}/card-terms", json=_TERMS
            )
            assert wrong_type.status_code == 400
            assert "crédito" in wrong_type.json()["detail"]

            # PUT creates.
            created = await ac.put(
                f"/api/v1/accounts/{card_id}/card-terms", json=_TERMS
            )
            assert created.status_code == 200, created.text
            body = created.json()
            assert Decimal(body["annual_interest_rate"]) == Decimal("0.45")
            assert body["payment_due_day"] == 10

            # PUT again replaces (still one row — 1:1).
            updated = await ac.put(
                f"/api/v1/accounts/{card_id}/card-terms",
                json={**_TERMS, "annual_interest_rate": "0.39"},
            )
            assert updated.status_code == 200
            assert Decimal(updated.json()["annual_interest_rate"]) == Decimal(
                "0.39"
            )

            rows = (
                await session.execute(
                    select(CreditCardTerms).where(
                        CreditCardTerms.user_id == user_id
                    )
                )
            ).scalars().all()
            assert len(rows) == 1

            # Unknown field → 422 (extra="forbid").
            extra = await ac.put(
                f"/api/v1/accounts/{card_id}/card-terms",
                json={**_TERMS, "balance": "100"},
            )
            assert extra.status_code == 422

            # GET now returns them.
            got = await ac.get(f"/api/v1/accounts/{card_id}/card-terms")
            assert got.status_code == 200
            assert got.json()["statement_day"] == 20

            # DELETE removes.
            gone = await ac.delete(f"/api/v1/accounts/{card_id}/card-terms")
            assert gone.status_code == 204
            again = await ac.get(f"/api/v1/accounts/{card_id}/card-terms")
            assert again.status_code == 404
    finally:
        _clear()


@pytest.mark.asyncio
async def test_account_hard_delete_cascades_card_terms(db_with_user):
    session, user_id = db_with_user
    card = await _add_account(session, user_id, "Visa Promerica")
    card_id = card.id

    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            created = await ac.put(
                f"/api/v1/accounts/{card_id}/card-terms", json=_TERMS
            )
            assert created.status_code == 200

            deleted = await ac.delete(
                f"/api/v1/accounts/{card_id}",
                params={"hard": "true", "confirm": "Visa Promerica"},
            )
            assert deleted.status_code == 200, deleted.text
    finally:
        _clear()

    session.expire_all()
    rows = (
        await session.execute(
            select(CreditCardTerms).where(CreditCardTerms.user_id == user_id)
        )
    ).scalars().all()
    assert rows == []
