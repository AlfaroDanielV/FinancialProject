from __future__ import annotations

import secrets
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.queries.tools.accounts import get_account_balance, list_accounts
from api.models.account import Account
from api.models.transaction import Transaction
from api.models.user import User


async def _purge_user(session, uid):
    """Manual cleanup for secondary users created in isolation tests."""
    for stmt in (
        "DELETE FROM transactions WHERE user_id = :u",
        "DELETE FROM accounts WHERE user_id = :u",
        "DELETE FROM users WHERE id = :u",
    ):
        await session.execute(text(stmt), {"u": uid})
    await session.commit()


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _patch_session(monkeypatch, session, module="accounts"):
    monkeypatch.setattr(
        f"app.queries.tools.{module}.AsyncSessionLocal",
        lambda: _SessionContext(session),
    )


async def _add_account(session, user_id, name, account_type="checking", is_active=True):
    a = Account(
        user_id=user_id, name=name, account_type=account_type, is_active=is_active
    )
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return a


async def _add_txn(
    session, user_id, account_id, amount, txn_date=date(2026, 4, 20)
):
    session.add(
        Transaction(
            user_id=user_id,
            account_id=account_id,
            amount=Decimal(amount),
            currency="CRC",
            merchant="seed",
            category="seed",
            transaction_date=txn_date,
            created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
            source="manual",
        )
    )
    await session.commit()


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
async def test_get_account_balance_no_filter_returns_all_with_total(
    db_with_user, monkeypatch
):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    a = await _add_account(session, user_id, "BAC Débito", "checking")
    b = await _add_account(session, user_id, "Promerica Visa", "credit")
    await _add_txn(session, user_id, a.id, "100000")
    await _add_txn(session, user_id, a.id, "-25000")
    await _add_txn(session, user_id, b.id, "-50000")

    result = await get_account_balance(user_id=user_id)
    assert result["matched_count"] == 2
    assert result["currency"] == "CRC"
    assert result["total_balance"] == "25000"
    by_name = {row["account_name"]: row for row in result["accounts"]}
    assert by_name["BAC Débito"]["current_balance"] == "75000"
    assert by_name["Promerica Visa"]["current_balance"] == "-50000"
    assert by_name["BAC Débito"]["last_transaction_date"] == "2026-04-20"


@pytest.mark.asyncio
async def test_get_account_balance_fuzzy_match_and_no_match(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    bac = await _add_account(session, user_id, "BAC Débito", "checking")
    await _add_account(session, user_id, "Promerica", "checking")
    await _add_txn(session, user_id, bac.id, "10000")

    fuzzy = await get_account_balance(user_id=user_id, account_name="bac")
    assert fuzzy["matched_count"] == 1
    assert fuzzy["accounts"][0]["account_name"] == "BAC Débito"
    assert fuzzy["total_balance"] == fuzzy["accounts"][0]["current_balance"]

    miss = await get_account_balance(user_id=user_id, account_name="cuenta inexistente")
    assert miss["matched_count"] == 0
    assert miss["accounts"] == []
    assert miss["total_balance"] == "0"


@pytest.mark.asyncio
async def test_get_account_balance_credit_negative_and_no_transactions(
    db_with_user, monkeypatch
):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    credit = await _add_account(session, user_id, "BAC Visa", "credit")
    fresh = await _add_account(session, user_id, "Cuenta nueva", "checking")
    await _add_txn(session, user_id, credit.id, "-245000")

    result = await get_account_balance(user_id=user_id, account_name="BAC Visa")
    row = result["accounts"][0]
    assert row["current_balance"] == "-245000"
    assert row["account_type"] == "credit"

    fresh_result = await get_account_balance(
        user_id=user_id, account_name="Cuenta nueva"
    )
    assert fresh_result["accounts"][0]["current_balance"] == "0"
    assert fresh_result["accounts"][0]["last_transaction_date"] is None
    assert fresh.id


@pytest.mark.asyncio
async def test_get_account_balance_user_isolation(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    other_uid = await _seed_other_user(session)
    try:
        mine = await _add_account(session, user_id, "Mía", "checking")
        other = Account(user_id=other_uid, name="Suya", account_type="checking")
        session.add(other)
        await session.commit()
        await session.refresh(other)
        await _add_txn(session, user_id, mine.id, "10000")
        await _add_txn(session, other_uid, other.id, "999999")

        result = await get_account_balance(user_id=user_id)
        names = {row["account_name"] for row in result["accounts"]}
        assert names == {"Mía"}
        assert result["total_balance"] == "10000"
    finally:
        await _purge_user(session, other_uid)


@pytest.mark.asyncio
async def test_list_accounts_default_excludes_inactive(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session, module="accounts")
    await _add_account(session, user_id, "Activa", "checking", is_active=True)
    await _add_account(session, user_id, "Inactiva", "savings", is_active=False)

    default = await list_accounts(user_id=user_id)
    assert default["total_count"] == 2
    assert default["active_count"] == 1
    names = {a["account_name"] for a in default["accounts"]}
    assert names == {"Activa"}

    with_inactive = await list_accounts(user_id=user_id, include_inactive=True)
    names = {a["account_name"] for a in with_inactive["accounts"]}
    assert names == {"Activa", "Inactiva"}


@pytest.mark.asyncio
async def test_list_accounts_user_isolation(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session, module="accounts")
    other_uid = await _seed_other_user(session)
    try:
        await _add_account(session, user_id, "Mine", "checking")
        other = Account(user_id=other_uid, name="Theirs", account_type="checking")
        session.add(other)
        await session.commit()

        result = await list_accounts(user_id=user_id)
        assert {a["account_name"] for a in result["accounts"]} == {"Mine"}
        assert result["total_count"] == 1
    finally:
        await _purge_user(session, other_uid)
