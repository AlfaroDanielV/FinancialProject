from __future__ import annotations

import secrets
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.queries.tools.compare_periods import InvalidPeriod, compare_periods
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


def _patch_session(monkeypatch, session):
    # compare_periods imports AsyncSessionLocal from api.database; patch
    # the module-local symbol where it's looked up at call time.
    monkeypatch.setattr(
        "app.queries.tools.compare_periods.AsyncSessionLocal",
        lambda: _SessionContext(session),
    )


async def _add_account(session, user_id, name="Default"):
    a = Account(user_id=user_id, name=name, account_type="checking")
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return a


async def _add_txn(
    session,
    *,
    user_id,
    account_id,
    amount,
    txn_date,
    merchant="seed",
    category="seed",
):
    session.add(
        Transaction(
            user_id=user_id,
            account_id=account_id,
            amount=Decimal(amount),
            currency="CRC",
            merchant=merchant,
            category=category,
            transaction_date=txn_date,
            created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
            source="manual",
        )
    )
    await session.commit()


async def _seed_two_periods(session, user_id) -> Account:
    """March: 4 expense rows summing 800. April: 5 rows summing 1300.

    Categories — march: supermercado(300+200), transporte(200), entretenimiento(100).
    April: supermercado(400+200), transporte(300), salud(250), entretenimiento(150).
    """
    acct = await _add_account(session, user_id, "Main")
    march = [
        ("-300", "Auto", "supermercado", date(2026, 3, 5)),
        ("-200", "Más x Menos", "supermercado", date(2026, 3, 12)),
        ("-200", "Uber", "transporte", date(2026, 3, 18)),
        ("-100", "Cinepolis", "entretenimiento", date(2026, 3, 25)),
    ]
    april = [
        ("-400", "PriceSmart", "supermercado", date(2026, 4, 3)),
        ("-200", "Más x Menos", "supermercado", date(2026, 4, 9)),
        ("-300", "Uber", "transporte", date(2026, 4, 14)),
        ("-250", "Farmacia Fischel", "salud", date(2026, 4, 20)),
        ("-150", "Cinepolis", "entretenimiento", date(2026, 4, 25)),
    ]
    for amount, merchant, category, txn_date in march + april:
        await _add_txn(
            session,
            user_id=user_id,
            account_id=acct.id,
            amount=amount,
            txn_date=txn_date,
            merchant=merchant,
            category=category,
        )
    return acct


async def _seed_other_user(session) -> uuid.UUID:
    u = User(
        email=f"other-{uuid.uuid4().hex}@example.com",
        full_name="Other",
        shortcut_token=secrets.token_urlsafe(48),
    )
    session.add(u)
    await session.commit()
    await session.refresh(u)
    return u.id


@pytest.mark.asyncio
async def test_compare_periods_no_group_by_returns_totals_and_delta(
    db_with_user, monkeypatch
):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _seed_two_periods(session, user_id)

    result = await compare_periods(
        user_id=user_id,
        period_a_start=date(2026, 3, 1),
        period_a_end=date(2026, 3, 31),
        period_b_start=date(2026, 4, 1),
        period_b_end=date(2026, 4, 30),
    )
    assert result["period_a"]["total_amount"] == "800"
    assert result["period_a"]["transaction_count"] == 4
    assert result["period_b"]["total_amount"] == "1300"
    assert result["period_b"]["transaction_count"] == 5
    assert result["delta_amount"] == "500"
    assert result["delta_percentage"] == 62.5
    assert result["transaction_type_filter"] == "expense"
    assert result["currency"] == "CRC"
    # No group_by => no groups in payload
    assert "groups" not in result["period_a"]
    assert "group_by" not in result


@pytest.mark.asyncio
async def test_compare_periods_with_group_by_independent_lists(
    db_with_user, monkeypatch
):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _seed_two_periods(session, user_id)

    result = await compare_periods(
        user_id=user_id,
        period_a_start=date(2026, 3, 1),
        period_a_end=date(2026, 3, 31),
        period_b_start=date(2026, 4, 1),
        period_b_end=date(2026, 4, 30),
        group_by="category",
    )
    assert result["group_by"] == "category"
    a_labels = {g["label"]: g for g in result["period_a"]["groups"]}
    b_labels = {g["label"]: g for g in result["period_b"]["groups"]}
    # March doesn't have "salud"; April does. The lists are independent.
    assert "salud" not in a_labels
    assert "salud" in b_labels
    assert a_labels["supermercado"]["amount"] == "500"
    assert a_labels["supermercado"]["count"] == 2
    assert b_labels["supermercado"]["amount"] == "600"
    assert b_labels["transporte"]["amount"] == "300"
    assert result["delta_amount"] == "500"


@pytest.mark.asyncio
async def test_compare_periods_period_a_zero_returns_null_percentage(
    db_with_user, monkeypatch
):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    acct = await _add_account(session, user_id)
    # Only seed period B
    await _add_txn(
        session, user_id=user_id, account_id=acct.id,
        amount="-12345", txn_date=date(2026, 4, 5),
    )

    result = await compare_periods(
        user_id=user_id,
        period_a_start=date(2026, 3, 1),
        period_a_end=date(2026, 3, 31),
        period_b_start=date(2026, 4, 1),
        period_b_end=date(2026, 4, 30),
    )
    assert result["period_a"]["total_amount"] == "0"
    assert result["period_a"]["transaction_count"] == 0
    assert result["period_b"]["total_amount"] == "12345"
    assert result["delta_amount"] == "12345"
    assert result["delta_percentage"] is None


@pytest.mark.asyncio
async def test_compare_periods_overlapping_ranges_no_error(
    db_with_user, monkeypatch
):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _seed_two_periods(session, user_id)

    # Overlapping: first half of April vs full April.
    result = await compare_periods(
        user_id=user_id,
        period_a_start=date(2026, 4, 1),
        period_a_end=date(2026, 4, 15),
        period_b_start=date(2026, 4, 1),
        period_b_end=date(2026, 4, 30),
    )
    # First half of April: 400+200+300 = 900 (3 txns)
    # Full April: 1300 (5 txns)
    assert result["period_a"]["total_amount"] == "900"
    assert result["period_a"]["transaction_count"] == 3
    assert result["period_b"]["total_amount"] == "1300"
    assert result["delta_amount"] == "400"


@pytest.mark.asyncio
async def test_compare_periods_invalid_range_raises(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    with pytest.raises(InvalidPeriod) as exc:
        await compare_periods(
            user_id=user_id,
            period_a_start=date(2026, 4, 30),
            period_a_end=date(2026, 4, 1),  # start > end
            period_b_start=date(2026, 5, 1),
            period_b_end=date(2026, 5, 31),
        )
    msg = str(exc.value)
    assert "period_a_start" in msg
    assert "2026-04-30" in msg


@pytest.mark.asyncio
async def test_compare_periods_income_filter_works_in_both_periods(
    db_with_user, monkeypatch
):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    acct = await _add_account(session, user_id)
    # Mix: each period has 1 income + 1 expense.
    await _add_txn(
        session, user_id=user_id, account_id=acct.id,
        amount="100000", txn_date=date(2026, 3, 15), category="salario",
    )
    await _add_txn(
        session, user_id=user_id, account_id=acct.id,
        amount="-50000", txn_date=date(2026, 3, 16),
    )
    await _add_txn(
        session, user_id=user_id, account_id=acct.id,
        amount="120000", txn_date=date(2026, 4, 15), category="salario",
    )
    await _add_txn(
        session, user_id=user_id, account_id=acct.id,
        amount="-60000", txn_date=date(2026, 4, 16),
    )

    result = await compare_periods(
        user_id=user_id,
        period_a_start=date(2026, 3, 1),
        period_a_end=date(2026, 3, 31),
        period_b_start=date(2026, 4, 1),
        period_b_end=date(2026, 4, 30),
        transaction_type="income",
    )
    assert result["transaction_type_filter"] == "income"
    assert result["period_a"]["total_amount"] == "100000"
    assert result["period_a"]["transaction_count"] == 1
    assert result["period_b"]["total_amount"] == "120000"
    assert result["period_b"]["transaction_count"] == 1


@pytest.mark.asyncio
async def test_compare_periods_combined_filters_apply_to_both(
    db_with_user, monkeypatch
):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    main = await _add_account(session, user_id, "Main")
    side = await _add_account(session, user_id, "Side")
    # Both accounts have data in both months; filter to only Main + supermercado.
    await _add_txn(
        session, user_id=user_id, account_id=main.id,
        amount="-300", txn_date=date(2026, 3, 5),
        merchant="Auto", category="supermercado",
    )
    await _add_txn(
        session, user_id=user_id, account_id=side.id,
        amount="-999", txn_date=date(2026, 3, 5),
        merchant="Side", category="supermercado",
    )
    await _add_txn(
        session, user_id=user_id, account_id=main.id,
        amount="-100", txn_date=date(2026, 3, 10),
        merchant="Auto", category="transporte",
    )
    await _add_txn(
        session, user_id=user_id, account_id=main.id,
        amount="-400", txn_date=date(2026, 4, 5),
        merchant="Auto", category="supermercado",
    )
    await _add_txn(
        session, user_id=user_id, account_id=side.id,
        amount="-888", txn_date=date(2026, 4, 5),
        merchant="Side", category="supermercado",
    )

    result = await compare_periods(
        user_id=user_id,
        period_a_start=date(2026, 3, 1),
        period_a_end=date(2026, 3, 31),
        period_b_start=date(2026, 4, 1),
        period_b_end=date(2026, 4, 30),
        account_ids=[main.id],
        categories=["supermercado"],
        group_by="category",
    )
    # Main + supermercado only:
    # March: 300; April: 400. Side and transporte excluded.
    assert result["period_a"]["total_amount"] == "300"
    assert result["period_b"]["total_amount"] == "400"
    a_groups = {g["label"]: g for g in result["period_a"]["groups"]}
    b_groups = {g["label"]: g for g in result["period_b"]["groups"]}
    assert set(a_groups) == {"supermercado"}
    assert set(b_groups) == {"supermercado"}


@pytest.mark.asyncio
async def test_compare_periods_user_isolation(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    other_uid = await _seed_other_user(session)
    try:
        mine = await _add_account(session, user_id, "Mía")
        theirs = Account(
            user_id=other_uid, name="Suya", account_type="checking"
        )
        session.add(theirs)
        await session.commit()
        await session.refresh(theirs)
        await _add_txn(
            session, user_id=user_id, account_id=mine.id,
            amount="-500", txn_date=date(2026, 3, 5),
        )
        await _add_txn(
            session, user_id=other_uid, account_id=theirs.id,
            amount="-99999", txn_date=date(2026, 3, 5),
        )
        await _add_txn(
            session, user_id=user_id, account_id=mine.id,
            amount="-700", txn_date=date(2026, 4, 5),
        )
        await _add_txn(
            session, user_id=other_uid, account_id=theirs.id,
            amount="-99999", txn_date=date(2026, 4, 5),
        )

        result = await compare_periods(
            user_id=user_id,
            period_a_start=date(2026, 3, 1),
            period_a_end=date(2026, 3, 31),
            period_b_start=date(2026, 4, 1),
            period_b_end=date(2026, 4, 30),
        )
        assert result["period_a"]["total_amount"] == "500"
        assert result["period_b"]["total_amount"] == "700"
    finally:
        for stmt in (
            "DELETE FROM transactions WHERE user_id = :u",
            "DELETE FROM accounts WHERE user_id = :u",
            "DELETE FROM users WHERE id = :u",
        ):
            await session.execute(text(stmt), {"u": other_uid})
        await session.commit()
