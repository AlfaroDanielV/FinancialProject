from __future__ import annotations

import secrets
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.queries.tools.debts import (
    DebtNotFound,
    get_debt_details,
    list_debts,
)
from api.models.debt import Debt
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
        "app.queries.tools.debts.AsyncSessionLocal",
        lambda: _SessionContext(session),
    )


async def _add_debt(
    session,
    user_id,
    *,
    name,
    debt_type="personal_loan",
    original=Decimal("1000000"),
    balance=Decimal("800000"),
    rate=Decimal("0.0850"),  # 8.5%
    min_payment=Decimal("50000"),
    due_day=15,
    term_months=24,
    payments_made=4,
    is_active=True,
    currency="CRC",
):
    d = Debt(
        user_id=user_id,
        name=name,
        debt_type=debt_type,
        original_amount=original,
        current_balance=balance,
        interest_rate=rate,
        minimum_payment=min_payment,
        payment_due_day=due_day,
        term_months=term_months,
        payments_made=payments_made,
        is_active=is_active,
        currency=currency,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    session.add(d)
    await session.commit()
    await session.refresh(d)
    return d


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
async def test_list_debts_active_filter_and_totals(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _add_debt(
        session, user_id, name="Préstamo casa BAC", debt_type="mortgage",
        balance=Decimal("12500000"), min_payment=Decimal("185000"),
        rate=Decimal("0.0850"), term_months=240, payments_made=14,
    )
    await _add_debt(
        session, user_id, name="Tarjeta", debt_type="credit_card",
        balance=Decimal("450000"), min_payment=Decimal("80000"),
        rate=Decimal("0.4500"), term_months=None, payments_made=0,
    )
    await _add_debt(
        session, user_id, name="Pagada", debt_type="auto_loan",
        balance=Decimal("0"), min_payment=Decimal("0"),
        is_active=False,
    )

    active = await list_debts(user_id=user_id, status="active")
    assert active["total_count"] == 2
    assert active["total_current_balance"] == "12950000"
    assert active["total_monthly_payment"] == "265000"

    by_name = {d["debt_name"]: d for d in active["debts"]}
    assert by_name["Préstamo casa BAC"]["interest_rate_annual"] == "8.5"
    assert by_name["Préstamo casa BAC"]["payments_remaining"] == 226
    assert by_name["Tarjeta"]["payments_remaining"] is None
    assert by_name["Tarjeta"]["interest_rate_annual"] == "45"

    paid_off = await list_debts(user_id=user_id, status="paid_off")
    assert {d["debt_name"] for d in paid_off["debts"]} == {"Pagada"}


@pytest.mark.asyncio
async def test_list_debts_no_schedule_returns_nulls(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _add_debt(
        session, user_id, name="A primo", debt_type="other",
        balance=Decimal("200000"), min_payment=Decimal("0"),
        term_months=None, payments_made=0,
    )

    result = await list_debts(user_id=user_id)
    row = result["debts"][0]
    assert row["monthly_payment"] is None
    assert row["payments_remaining"] is None
    assert result["total_monthly_payment"] == "0"


@pytest.mark.asyncio
async def test_get_debt_details_exact_match_includes_projection(
    db_with_user, monkeypatch
):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _add_debt(
        session, user_id, name="Préstamo casa BAC", debt_type="mortgage",
        balance=Decimal("12500000"), min_payment=Decimal("185000"),
        rate=Decimal("0.0850"), term_months=240, payments_made=14,
    )

    result = await get_debt_details(user_id=user_id, debt_name="Préstamo casa BAC")
    assert result["debt_name"] == "Préstamo casa BAC"
    assert result["monthly_payment"] == "185000"
    assert result["interest_rate_annual"] == "8.5"
    assert result["estimated_payoff_date"] is not None
    assert result["total_interest_remaining"] is not None
    payoff_year = int(result["estimated_payoff_date"][:4])
    assert payoff_year >= datetime.now(timezone.utc).year + 5


@pytest.mark.asyncio
async def test_get_debt_details_fuzzy_match(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _add_debt(
        session, user_id, name="Préstamo casa BAC", debt_type="mortgage",
        balance=Decimal("12500000"), min_payment=Decimal("185000"),
        term_months=240,
    )
    result = await get_debt_details(user_id=user_id, debt_name="casa")
    assert result["debt_name"] == "Préstamo casa BAC"


@pytest.mark.asyncio
async def test_get_debt_details_no_match_raises(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _add_debt(
        session, user_id, name="Préstamo casa", debt_type="mortgage",
        balance=Decimal("100"), min_payment=Decimal("10"),
    )
    with pytest.raises(DebtNotFound) as exc:
        await get_debt_details(user_id=user_id, debt_name="hipoteca avión")
    assert "list_debts" in str(exc.value)


@pytest.mark.asyncio
async def test_get_debt_details_no_schedule_returns_null_projection(
    db_with_user, monkeypatch
):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _add_debt(
        session, user_id, name="Plata a primo", debt_type="other",
        balance=Decimal("200000"), min_payment=Decimal("0"),
        term_months=None, payments_made=0,
    )
    result = await get_debt_details(user_id=user_id, debt_name="primo")
    assert result["estimated_payoff_date"] is None
    assert result["total_interest_remaining"] is None
    assert result["monthly_payment"] is None


@pytest.mark.asyncio
async def test_list_debts_user_isolation(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    other_uid = await _seed_other_user(session)
    try:
        await _add_debt(session, user_id, name="Mía", balance=Decimal("100"))
        other = Debt(
            user_id=other_uid, name="Suya", debt_type="personal_loan",
            original_amount=Decimal("999"), current_balance=Decimal("999"),
            interest_rate=Decimal("0.05"), minimum_payment=Decimal("10"),
            payment_due_day=1, term_months=12, payments_made=0, is_active=True,
            currency="CRC",
        )
        session.add(other)
        await session.commit()

        result = await list_debts(user_id=user_id)
        assert {d["debt_name"] for d in result["debts"]} == {"Mía"}
    finally:
        for stmt in (
            "DELETE FROM debts WHERE user_id = :u",
            "DELETE FROM users WHERE id = :u",
        ):
            await session.execute(text(stmt), {"u": other_uid})
        await session.commit()
