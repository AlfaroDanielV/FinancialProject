from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.queries.tools.transactions import list_transactions
from api.models.account import Account
from api.models.transaction import Transaction
from api.models.user import User


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return None


async def _seed_account(session, user_id: uuid.UUID, name: str) -> Account:
    account = Account(user_id=user_id, name=name, account_type="checking")
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _seed_txn(
    session,
    *,
    user_id: uuid.UUID,
    account_id: uuid.UUID | None,
    amount: str,
    merchant: str,
    category: str,
    transaction_date: date,
    created_at: datetime,
    currency: str = "CRC",
    description: str | None = None,
) -> Transaction:
    txn = Transaction(
        user_id=user_id,
        account_id=account_id,
        amount=Decimal(amount),
        currency=currency,
        merchant=merchant,
        category=category,
        description=description,
        transaction_date=transaction_date,
        created_at=created_at,
        source="manual",
    )
    session.add(txn)
    await session.commit()
    await session.refresh(txn)
    return txn


async def _seed_list_data(session, user_id: uuid.UUID) -> tuple[Account, Account]:
    user = await session.get(User, user_id)
    user.currency = "USD"
    account_a = await _seed_account(session, user_id, "Promerica Visa")
    account_b = await _seed_account(session, user_id, "BAC Débito")

    await _seed_txn(
        session,
        user_id=user_id,
        account_id=account_a.id,
        amount="-1000",
        merchant="PriceSmart",
        category="supermercado",
        transaction_date=date(2026, 4, 20),
        created_at=datetime(2026, 4, 20, 9, tzinfo=timezone.utc),
        description="compra semanal",
    )
    await _seed_txn(
        session,
        user_id=user_id,
        account_id=account_a.id,
        amount="-5000",
        merchant="Más x Menos",
        category="supermercado",
        transaction_date=date(2026, 4, 21),
        created_at=datetime(2026, 4, 21, 10, tzinfo=timezone.utc),
    )
    await _seed_txn(
        session,
        user_id=user_id,
        account_id=account_b.id,
        amount="200000",
        merchant="Empresa",
        category="salario",
        transaction_date=date(2026, 4, 21),
        created_at=datetime(2026, 4, 21, 11, tzinfo=timezone.utc),
    )
    await _seed_txn(
        session,
        user_id=user_id,
        account_id=account_b.id,
        amount="-2500",
        merchant="Uber",
        category="transporte",
        transaction_date=date(2026, 4, 22),
        created_at=datetime(2026, 4, 22, 8, tzinfo=timezone.utc),
    )
    await _seed_txn(
        session,
        user_id=user_id,
        account_id=account_b.id,
        amount="-7500",
        merchant="PriceSmart",
        category="supermercado",
        transaction_date=date(2026, 4, 23),
        created_at=datetime(2026, 4, 23, 8, tzinfo=timezone.utc),
    )
    await _seed_txn(
        session,
        user_id=user_id,
        account_id=account_a.id,
        amount="-300",
        merchant="Pulpería La Esquina",
        category="snacks",
        transaction_date=date(2026, 4, 24),
        created_at=datetime(2026, 4, 24, 8, tzinfo=timezone.utc),
    )
    await _seed_txn(
        session,
        user_id=user_id,
        account_id=account_a.id,
        amount="-999",
        merchant="Fuera de rango",
        category="otros",
        transaction_date=date(2026, 4, 19),
        created_at=datetime(2026, 4, 19, 8, tzinfo=timezone.utc),
    )
    await session.commit()
    return account_a, account_b


def _merchants(result: dict[str, Any]) -> list[str | None]:
    return [txn["merchant"] for txn in result["transactions"]]


@pytest.mark.asyncio
async def test_list_transactions_filters_dates_accounts_fuzzy_type_amounts_and_currency(
    db_with_user,
    monkeypatch,
):
    session, user_id = db_with_user
    monkeypatch.setattr(
        "app.queries.tools.transactions.AsyncSessionLocal",
        lambda: _SessionContext(session),
    )
    account_a, account_b = await _seed_list_data(session, user_id)

    by_date = await list_transactions(
        user_id=user_id,
        start_date=date(2026, 4, 20),
        end_date=date(2026, 4, 23),
        transaction_type="expense",
    )
    assert by_date["total_matched"] == 4
    assert by_date["total_amount"] == "16000"
    assert by_date["currency"] == "USD"
    assert {txn["currency"] for txn in by_date["transactions"]} == {"USD"}

    by_account = await list_transactions(
        user_id=user_id,
        start_date=date(2026, 4, 20),
        end_date=date(2026, 4, 24),
        account_ids=[account_b.id],
        transaction_type="expense",
    )
    assert by_account["total_matched"] == 2
    assert {txn["account_name"] for txn in by_account["transactions"]} == {
        "BAC Débito"
    }

    by_merchant = await list_transactions(
        user_id=user_id,
        start_date=date(2026, 4, 20),
        end_date=date(2026, 4, 24),
        merchants=["price smart"],
        transaction_type="expense",
    )
    assert by_merchant["total_matched"] == 2
    assert _merchants(by_merchant) == ["PriceSmart", "PriceSmart"]

    by_category = await list_transactions(
        user_id=user_id,
        start_date=date(2026, 4, 20),
        end_date=date(2026, 4, 24),
        categories=["supermercad"],
        transaction_type="expense",
    )
    assert by_category["total_matched"] == 3

    income = await list_transactions(
        user_id=user_id,
        start_date=date(2026, 4, 20),
        end_date=date(2026, 4, 24),
        transaction_type="income",
    )
    assert income["total_matched"] == 1
    assert income["transactions"][0]["transaction_type"] == "income"
    assert income["transactions"][0]["amount"] == "200000"

    amount_window = await list_transactions(
        user_id=user_id,
        start_date=date(2026, 4, 20),
        end_date=date(2026, 4, 24),
        transaction_type="expense",
        min_amount=Decimal("1000"),
        max_amount=Decimal("5000"),
    )
    assert amount_window["total_matched"] == 3
    assert set(_merchants(amount_window)) == {"PriceSmart", "Más x Menos", "Uber"}

    limited = await list_transactions(
        user_id=user_id,
        start_date=date(2026, 4, 20),
        end_date=date(2026, 4, 24),
        transaction_type="expense",
        limit=2,
        sort="amount_desc",
    )
    assert limited["total_matched"] == 5
    assert limited["limit_applied"] == 2
    assert limited["truncated"] is True
    assert _merchants(limited) == ["PriceSmart", "Más x Menos"]

    assert account_a.id


@pytest.mark.asyncio
async def test_list_transactions_sort_options_and_output_shape(db_with_user, monkeypatch):
    session, user_id = db_with_user
    monkeypatch.setattr(
        "app.queries.tools.transactions.AsyncSessionLocal",
        lambda: _SessionContext(session),
    )
    await _seed_list_data(session, user_id)

    date_desc = await list_transactions(
        user_id=user_id,
        start_date=date(2026, 4, 20),
        end_date=date(2026, 4, 24),
        transaction_type="expense",
        sort="date_desc",
    )
    assert _merchants(date_desc) == [
        "Pulpería La Esquina",
        "PriceSmart",
        "Uber",
        "Más x Menos",
        "PriceSmart",
    ]

    date_asc = await list_transactions(
        user_id=user_id,
        start_date=date(2026, 4, 20),
        end_date=date(2026, 4, 24),
        transaction_type="expense",
        sort="date_asc",
    )
    assert _merchants(date_asc) == [
        "PriceSmart",
        "Más x Menos",
        "Uber",
        "PriceSmart",
        "Pulpería La Esquina",
    ]

    amount_desc = await list_transactions(
        user_id=user_id,
        start_date=date(2026, 4, 20),
        end_date=date(2026, 4, 24),
        transaction_type="expense",
        sort="amount_desc",
    )
    assert [txn["amount"] for txn in amount_desc["transactions"]] == [
        "7500",
        "5000",
        "2500",
        "1000",
        "300",
    ]

    amount_asc = await list_transactions(
        user_id=user_id,
        start_date=date(2026, 4, 20),
        end_date=date(2026, 4, 24),
        transaction_type="expense",
        sort="amount_asc",
    )
    assert [txn["amount"] for txn in amount_asc["transactions"]] == [
        "300",
        "1000",
        "2500",
        "5000",
        "7500",
    ]

    first = amount_desc["transactions"][0]
    assert "id" not in first
    assert "created_at" not in first
    assert "occurred_at" not in first
    assert "transaction_date" in first
