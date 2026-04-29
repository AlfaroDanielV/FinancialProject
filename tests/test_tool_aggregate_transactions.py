from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.queries.tools.transactions import aggregate_transactions
from api.models.account import Account
from api.models.transaction import Transaction


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
) -> None:
    session.add(
        Transaction(
            user_id=user_id,
            account_id=account_id,
            amount=Decimal(amount),
            currency="CRC",
            merchant=merchant,
            category=category,
            transaction_date=transaction_date,
            created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
            source="manual",
        )
    )
    await session.commit()


async def _seed_aggregate_data(session, user_id: uuid.UUID) -> tuple[Account, Account]:
    account_a = await _seed_account(session, user_id, "Promerica")
    account_b = await _seed_account(session, user_id, "BAC")

    await _seed_txn(
        session,
        user_id=user_id,
        account_id=account_a.id,
        amount="-100",
        merchant="PriceSmart",
        category="supermercado",
        transaction_date=date(2026, 4, 21),
    )
    await _seed_txn(
        session,
        user_id=user_id,
        account_id=account_a.id,
        amount="-200",
        merchant="Más x Menos",
        category="supermercado",
        transaction_date=date(2026, 4, 21),
    )
    await _seed_txn(
        session,
        user_id=user_id,
        account_id=account_b.id,
        amount="-300",
        merchant="Uber",
        category="transporte",
        transaction_date=date(2026, 4, 21),
    )
    await _seed_txn(
        session,
        user_id=user_id,
        account_id=account_b.id,
        amount="-400",
        merchant="PriceSmart",
        category="supermercado",
        transaction_date=date(2026, 4, 22),
    )
    await _seed_txn(
        session,
        user_id=user_id,
        account_id=account_a.id,
        amount="-500",
        merchant="Cine",
        category="ocio",
        transaction_date=date(2026, 4, 22),
    )
    await _seed_txn(
        session,
        user_id=user_id,
        account_id=account_a.id,
        amount="1000",
        merchant="Empresa",
        category="salario",
        transaction_date=date(2026, 4, 21),
    )
    await _seed_txn(
        session,
        user_id=user_id,
        account_id=account_a.id,
        amount="-50",
        merchant="Domingo previo",
        category="transporte",
        transaction_date=date(2026, 4, 19),
    )
    await _seed_txn(
        session,
        user_id=user_id,
        account_id=account_a.id,
        amount="-60",
        merchant="Lunes",
        category="transporte",
        transaction_date=date(2026, 4, 20),
    )
    await _seed_txn(
        session,
        user_id=user_id,
        account_id=account_a.id,
        amount="-70",
        merchant="Domingo",
        category="salud",
        transaction_date=date(2026, 4, 26),
    )
    await _seed_txn(
        session,
        user_id=user_id,
        account_id=account_a.id,
        amount="-80",
        merchant="Marzo",
        category="supermercado",
        transaction_date=date(2026, 3, 31),
    )
    await _seed_txn(
        session,
        user_id=user_id,
        account_id=account_b.id,
        amount="-90",
        merchant="Abril",
        category="transporte",
        transaction_date=date(2026, 4, 1),
    )
    return account_a, account_b


def _by_label(result):
    return {group["label"]: group for group in result["groups"]}


@pytest.mark.asyncio
async def test_aggregate_transactions_groups_by_category_account_merchant_and_day(
    db_with_user,
    monkeypatch,
):
    session, user_id = db_with_user
    monkeypatch.setattr(
        "app.queries.tools.transactions.AsyncSessionLocal",
        lambda: _SessionContext(session),
    )
    account_a, account_b = await _seed_aggregate_data(session, user_id)

    by_category = await aggregate_transactions(
        user_id=user_id,
        start_date=date(2026, 4, 21),
        end_date=date(2026, 4, 22),
        group_by="category",
        transaction_type="expense",
        sort="amount_desc",
    )
    categories = _by_label(by_category)
    assert by_category["grand_total"] == "1500"
    assert by_category["total_groups"] == 3
    assert categories["supermercado"]["amount"] == "700"
    assert categories["supermercado"]["count"] == 3
    assert categories["transporte"]["amount"] == "300"

    by_account = await aggregate_transactions(
        user_id=user_id,
        start_date=date(2026, 4, 21),
        end_date=date(2026, 4, 22),
        group_by="account",
        transaction_type="expense",
        sort="label_asc",
    )
    accounts = _by_label(by_account)
    assert accounts["Promerica"]["amount"] == "800"
    assert accounts["BAC"]["amount"] == "700"

    by_merchant = await aggregate_transactions(
        user_id=user_id,
        start_date=date(2026, 4, 21),
        end_date=date(2026, 4, 22),
        group_by="merchant",
        transaction_type="expense",
        merchants=["price smart"],
    )
    merchants = _by_label(by_merchant)
    assert merchants["PriceSmart"]["amount"] == "500"
    assert merchants["PriceSmart"]["count"] == 2

    by_day = await aggregate_transactions(
        user_id=user_id,
        start_date=date(2026, 4, 21),
        end_date=date(2026, 4, 22),
        group_by="day",
        transaction_type="expense",
        sort="label_asc",
    )
    days = _by_label(by_day)
    assert days["2026-04-21"]["count"] == 3
    assert days["2026-04-22"]["count"] == 2

    filtered = await aggregate_transactions(
        user_id=user_id,
        start_date=date(2026, 4, 21),
        end_date=date(2026, 4, 22),
        group_by="category",
        account_ids=[account_b.id],
        categories=["supermercad"],
        merchants=["price smart"],
        transaction_type="expense",
    )
    assert filtered["grand_total"] == "400"
    assert filtered["groups"] == [
        {
            "label": "supermercado",
            "amount": "400",
            "count": 1,
            "percentage_of_total": 100.0,
        }
    ]
    assert account_a.id


@pytest.mark.asyncio
async def test_aggregate_transactions_day_week_and_month_granularity(
    db_with_user,
    monkeypatch,
):
    session, user_id = db_with_user
    monkeypatch.setattr(
        "app.queries.tools.transactions.AsyncSessionLocal",
        lambda: _SessionContext(session),
    )
    await _seed_aggregate_data(session, user_id)

    day = await aggregate_transactions(
        user_id=user_id,
        start_date=date(2026, 4, 21),
        end_date=date(2026, 4, 22),
        group_by="day",
        transaction_type="expense",
        sort="label_asc",
    )
    assert [(g["label"], g["count"]) for g in day["groups"]] == [
        ("2026-04-21", 3),
        ("2026-04-22", 2),
    ]

    same_week = await aggregate_transactions(
        user_id=user_id,
        start_date=date(2026, 4, 20),
        end_date=date(2026, 4, 26),
        group_by="week",
        transaction_type="expense",
        sort="label_asc",
    )
    weeks = _by_label(same_week)
    assert weeks["2026-04-20"]["count"] == 7
    assert same_week["total_groups"] == 1

    split_week = await aggregate_transactions(
        user_id=user_id,
        start_date=date(2026, 4, 19),
        end_date=date(2026, 4, 20),
        group_by="week",
        transaction_type="expense",
        sort="label_asc",
    )
    assert [(g["label"], g["count"]) for g in split_week["groups"]] == [
        ("2026-04-13", 1),
        ("2026-04-20", 1),
    ]

    month = await aggregate_transactions(
        user_id=user_id,
        start_date=date(2026, 3, 31),
        end_date=date(2026, 4, 1),
        group_by="month",
        transaction_type="expense",
        sort="label_asc",
    )
    assert [(g["label"], g["count"]) for g in month["groups"]] == [
        ("2026-03-01", 1),
        ("2026-04-01", 1),
    ]


@pytest.mark.asyncio
async def test_aggregate_transactions_top_n_other_percentage_and_all_type(
    db_with_user,
    monkeypatch,
):
    session, user_id = db_with_user
    monkeypatch.setattr(
        "app.queries.tools.transactions.AsyncSessionLocal",
        lambda: _SessionContext(session),
    )
    await _seed_aggregate_data(session, user_id)

    result = await aggregate_transactions(
        user_id=user_id,
        start_date=date(2026, 4, 21),
        end_date=date(2026, 4, 22),
        group_by="category",
        transaction_type="expense",
        top_n=2,
        sort="amount_desc",
    )
    assert result["grand_total"] == "1500"
    assert result["total_groups"] == 3
    assert result["other_amount"] == "300"
    assert result["other_count"] == 1
    assert result["groups"][0] == {
        "label": "supermercado",
        "amount": "700",
        "count": 3,
        "percentage_of_total": 46.7,
    }
    assert result["groups"][1]["percentage_of_total"] == 33.3

    all_types = await aggregate_transactions(
        user_id=user_id,
        start_date=date(2026, 4, 21),
        end_date=date(2026, 4, 22),
        group_by="category",
        transaction_type="all",
        sort="label_asc",
    )
    assert all_types["grand_total"] == "2500"
    assert all_types["transaction_type_filter"] == "all"
    assert _by_label(all_types)["salario"]["amount"] == "1000"
