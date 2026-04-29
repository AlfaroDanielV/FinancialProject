from __future__ import annotations

import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.queries.tools.recurring_bills import list_recurring_bills
from api.models.account import Account
from api.models.bill_occurrence import BillOccurrence
from api.models.recurring_bill import RecurringBill
from api.models.user import User


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _patch_session(monkeypatch, session):
    monkeypatch.setattr(
        "app.queries.tools.recurring_bills.AsyncSessionLocal",
        lambda: _SessionContext(session),
    )


def _today_cr() -> date:
    return datetime.now(ZoneInfo("America/Costa_Rica")).date()


async def _add_account(session, user_id, name="Default account"):
    a = Account(user_id=user_id, name=name, account_type="checking")
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return a


async def _add_bill(
    session, user_id, name, category, amount=Decimal("35000"), account_id=None
):
    b = RecurringBill(
        user_id=user_id,
        name=name,
        category=category,
        amount_expected=amount,
        currency="CRC",
        is_variable_amount=False,
        account_id=account_id,
        frequency="monthly",
        day_of_month=15,
        start_date=date(2026, 1, 1),
        is_active=True,
    )
    session.add(b)
    await session.commit()
    await session.refresh(b)
    return b


async def _add_occ(
    session, user_id, bill_id, *, due_date, status, amount=Decimal("35000"),
    paid_at=None,
):
    o = BillOccurrence(
        user_id=user_id,
        recurring_bill_id=bill_id,
        due_date=due_date,
        amount_expected=amount,
        status=status,
        paid_at=paid_at,
    )
    session.add(o)
    await session.commit()
    return o


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
async def test_list_recurring_bills_upcoming_filters_window(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    today = _today_cr()
    acct = await _add_account(session, user_id, "BAC")
    ice = await _add_bill(
        session, user_id, "ICE", "ice", Decimal("35000"), account_id=acct.id
    )
    netflix = await _add_bill(session, user_id, "Netflix", "subscription")
    far_future = await _add_bill(session, user_id, "Lejano", "other")

    await _add_occ(
        session, user_id, ice.id,
        due_date=today + timedelta(days=3),
        status="pending",
        amount=Decimal("35000"),
    )
    await _add_occ(
        session, user_id, netflix.id,
        due_date=today + timedelta(days=15),
        status="pending",
        amount=Decimal("12000"),
    )
    await _add_occ(
        session, user_id, far_future.id,
        due_date=today + timedelta(days=60),
        status="pending",
        amount=Decimal("99000"),
    )

    result = await list_recurring_bills(
        user_id=user_id, status="upcoming", days_ahead=7
    )
    names = [b["bill_name"] for b in result["bills"]]
    assert names == ["ICE"]
    assert result["bills"][0]["status"] == "upcoming"
    assert result["bills"][0]["account_name"] == "BAC"
    assert result["bills"][0]["days_until_due"] == 3
    assert result["total_amount_upcoming"] == "35000"


@pytest.mark.asyncio
async def test_list_recurring_bills_overdue(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    today = _today_cr()
    bill = await _add_bill(session, user_id, "Aya", "water")
    paid_bill = await _add_bill(session, user_id, "Pagado", "ice")

    await _add_occ(
        session, user_id, bill.id,
        due_date=today - timedelta(days=5),
        status="overdue",
        amount=Decimal("12000"),
    )
    await _add_occ(
        session, user_id, bill.id,
        due_date=today - timedelta(days=2),
        status="pending",  # pending + past due => overdue resolved
        amount=Decimal("8000"),
    )
    await _add_occ(
        session, user_id, paid_bill.id,
        due_date=today - timedelta(days=10),
        status="paid",
        amount=Decimal("35000"),
        paid_at=datetime.now(timezone.utc) - timedelta(days=8),
    )

    result = await list_recurring_bills(user_id=user_id, status="overdue")
    assert result["total_count"] == 2
    statuses = {b["status"] for b in result["bills"]}
    assert statuses == {"overdue"}
    assert all(b["days_until_due"] < 0 for b in result["bills"])
    assert result["total_amount_upcoming"] is None


@pytest.mark.asyncio
async def test_list_recurring_bills_paid_recently_window(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    today = _today_cr()
    bill = await _add_bill(session, user_id, "ICE", "ice")
    other_bill = await _add_bill(session, user_id, "Old payment", "ice")

    await _add_occ(
        session, user_id, bill.id,
        due_date=today - timedelta(days=5),
        status="paid",
        amount=Decimal("35000"),
        paid_at=datetime.now(timezone.utc) - timedelta(days=4),
    )
    await _add_occ(
        session, user_id, other_bill.id,
        due_date=today - timedelta(days=40),
        status="paid",
        amount=Decimal("35000"),
        paid_at=datetime.now(timezone.utc) - timedelta(days=30),
    )

    result = await list_recurring_bills(
        user_id=user_id, status="paid_recently", days_back=14
    )
    assert result["total_count"] == 1
    assert result["bills"][0]["bill_name"] == "ICE"
    assert result["bills"][0]["status"] == "paid"
    assert result["total_amount_upcoming"] is None


@pytest.mark.asyncio
async def test_list_recurring_bills_total_upcoming_only_sums_upcoming(
    db_with_user, monkeypatch
):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    today = _today_cr()
    bill = await _add_bill(session, user_id, "Múltiple", "other")

    await _add_occ(
        session, user_id, bill.id,
        due_date=today + timedelta(days=2),
        status="pending",
        amount=Decimal("10000"),
    )
    await _add_occ(
        session, user_id, bill.id,
        due_date=today + timedelta(days=5),
        status="pending",
        amount=Decimal("20000"),
    )

    result = await list_recurring_bills(
        user_id=user_id, status="upcoming", days_ahead=7
    )
    assert result["total_amount_upcoming"] == "30000"
    assert result["total_count"] == 2


@pytest.mark.asyncio
async def test_list_recurring_bills_user_isolation(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    today = _today_cr()
    other_uid = await _seed_other_user(session)
    try:
        mine = await _add_bill(session, user_id, "Mía", "ice")
        theirs = RecurringBill(
            user_id=other_uid, name="Suya", category="ice", currency="CRC",
            is_variable_amount=False, frequency="monthly", day_of_month=1,
            start_date=date(2026, 1, 1), is_active=True,
        )
        session.add(theirs)
        await session.commit()
        await session.refresh(theirs)
        await _add_occ(
            session, user_id, mine.id,
            due_date=today + timedelta(days=2), status="pending",
        )
        session.add(BillOccurrence(
            user_id=other_uid, recurring_bill_id=theirs.id,
            due_date=today + timedelta(days=2),
            amount_expected=Decimal("99999"), status="pending",
        ))
        await session.commit()

        result = await list_recurring_bills(
            user_id=user_id, status="upcoming", days_ahead=7
        )
        names = [b["bill_name"] for b in result["bills"]]
        assert names == ["Mía"]
    finally:
        for stmt in (
            "DELETE FROM bill_occurrences WHERE user_id = :u",
            "DELETE FROM recurring_bills WHERE user_id = :u",
            "DELETE FROM users WHERE id = :u",
        ):
            await session.execute(text(stmt), {"u": other_uid})
        await session.commit()
