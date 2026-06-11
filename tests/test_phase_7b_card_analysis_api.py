"""Phase 7b B4 — GET /accounts/{id}/card-analysis over the LIVE balance.

- 400 on non-credit account and on a credit account without terms.
- The analysis is computed from live transactions: charging the card moves
  balance_owed/minimum; paying it (transfer leg) moves them back.
- Minimum-trap surface: with a 45% APR and a 2.5% minimum (no floor),
  never_pays_off=true.
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
from api.models.transaction import Transaction


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


async def _charge(session, user_id, account_id, amount: str):
    txn = Transaction(
        user_id=user_id,
        account_id=account_id,
        amount=Decimal(amount),
        currency="CRC",
        transaction_date=date.today(),
        source="manual",
    )
    session.add(txn)
    await session.commit()


_TERMS = {
    "annual_interest_rate": "0.45",
    "minimum_payment_pct": "0.025",
    "minimum_payment_floor": "5000",
    "credit_limit": "1000000",
    "payment_due_day": 10,
}


@pytest.mark.asyncio
async def test_card_analysis_uses_live_balance(db_with_user):
    session, user_id = db_with_user
    card = await _add_account(session, user_id, "BAC Visa")
    card_id = card.id

    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # No terms yet → 400 telling the user to add them.
            missing = await ac.get(f"/api/v1/accounts/{card_id}/card-analysis")
            assert missing.status_code == 400

            put = await ac.put(
                f"/api/v1/accounts/{card_id}/card-terms", json=_TERMS
            )
            assert put.status_code == 200, put.text

            # Nothing owed yet.
            clean = await ac.get(f"/api/v1/accounts/{card_id}/card-analysis")
            assert clean.status_code == 200, clean.text
            body = clean.json()
            assert Decimal(body["balance_owed"]) == Decimal("0")
            assert Decimal(body["minimum_payment"]) == Decimal("0")
            assert body["never_pays_off"] is False
            assert Decimal(body["available_credit"]) == Decimal("1000000")

            # Charge ₡100.000 → owed and minimum move with the ledger.
            # At this balance the ₡5.000 floor beats 2.5% (₡2.500) AND beats
            # the month's interest (₡3.750), so the card eventually pays off.
            await _charge(session, user_id, card_id, "-100000")
            charged = await ac.get(f"/api/v1/accounts/{card_id}/card-analysis")
            body = charged.json()
            assert Decimal(body["balance_owed"]) == Decimal("100000")
            assert Decimal(body["minimum_payment"]) == Decimal("5000.00")
            assert Decimal(body["monthly_interest_cost"]) == Decimal("3750.00")
            assert Decimal(body["available_credit"]) == Decimal("900000")
            assert body["never_pays_off"] is False
            assert body["months_to_payoff_minimum"] is not None
            labels = [s["label"] for s in body["strategies"]]
            assert "Pagando el doble del mínimo" in labels
            assert "Para pagarla en 12 meses" in labels

            # A payment (credit leg) reduces what's owed.
            await _charge(session, user_id, card_id, "40000")
            paid = await ac.get(f"/api/v1/accounts/{card_id}/card-analysis")
            assert Decimal(paid.json()["balance_owed"]) == Decimal("60000")
    finally:
        _clear()


@pytest.mark.asyncio
async def test_card_analysis_minimum_trap_is_honest(db_with_user):
    session, user_id = db_with_user
    card = await _add_account(session, user_id, "Visa Promerica")
    card_id = card.id
    await _charge(session, user_id, card_id, "-500000")

    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            put = await ac.put(
                f"/api/v1/accounts/{card_id}/card-terms",
                json={
                    "annual_interest_rate": "0.45",
                    "minimum_payment_pct": "0.025",
                    "payment_due_day": 10,
                },
            )
            assert put.status_code == 200, put.text

            resp = await ac.get(f"/api/v1/accounts/{card_id}/card-analysis")
            body = resp.json()
            assert body["never_pays_off"] is True
            assert body["months_to_payoff_minimum"] is None
            assert body["total_interest_minimum"] is None
            # The 12-month strategy still gives a way out.
            twelve = next(
                s for s in body["strategies"] if "12 meses" in s["label"]
            )
            assert twelve["months"] == 12
    finally:
        _clear()


@pytest.mark.asyncio
async def test_card_analysis_rejects_non_credit(db_with_user):
    session, user_id = db_with_user
    checking = await _add_account(
        session, user_id, "Corriente", account_type="checking"
    )

    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/accounts/{checking.id}/card-analysis"
            )
            assert resp.status_code == 400
    finally:
        _clear()
