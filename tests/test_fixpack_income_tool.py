"""Fix pack B1 — registered-income query tool + debt-service ratio.

The dispatcher used to ask "¿cuánto ganás al mes?" even with a salary on file
because no read-only tool surfaced registered income. `list_registered_income`
reuses the single `_monthly_income` normalizer (same-currency, quincenal ×2,
aguinaldo/salario-escolar excluded); `get_savings_capacity` now also returns a
deterministic `debt_to_income_ratio` so "¿me alcanza el salario para mis
deudas?" answers with a real number. Prereqs: `docker compose up -d db &&
alembic upgrade head`.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from api.models.debt import Debt
from api.models.envelope import Envelope
from api.models.recurring_income import RecurringIncome
from app.queries.tools.affordability import get_savings_capacity
from app.queries.tools.income import list_registered_income

_TODAY = date(2026, 6, 1)


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _patch_session(monkeypatch, module, session):
    monkeypatch.setattr(
        f"app.queries.tools.{module}.AsyncSessionLocal",
        lambda: _SessionContext(session),
    )


def _income(user_id, **overrides) -> RecurringIncome:
    base = dict(
        user_id=user_id,
        name="Salario",
        income_type="salary",
        amount=Decimal("800000"),
        currency="CRC",
        frequency="monthly",
        next_payment_date=_TODAY,
    )
    base.update(overrides)
    return RecurringIncome(**base)


@pytest.mark.asyncio
async def test_quincenal_normalized_to_monthly(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, "income", session)
    # Per-payment 400,000 stored; quincenal = 2 payments/month → 800,000/month.
    session.add(_income(user_id, amount=Decimal("400000"), frequency="biweekly"))
    await session.commit()

    r = await list_registered_income(user_id=user_id)
    assert r["monthly_income"] == "800000.00"
    assert len(r["incomes"]) == 1
    row = r["incomes"][0]
    assert row["counted_in_monthly"] is True
    assert row["monthly_equivalent"] == "800000.00"
    assert row["amount_per_payment"] == "400000.00"


@pytest.mark.asyncio
async def test_usd_income_listed_not_summed(db_with_user, monkeypatch):
    session, user_id = db_with_user  # user currency is CRC
    _patch_session(monkeypatch, "income", session)
    session.add(_income(user_id, name="Salario CRC", amount=Decimal("500000")))
    session.add(
        _income(
            user_id,
            name="Freelance USD",
            income_type="freelance",
            amount=Decimal("1000"),
            currency="USD",
        )
    )
    await session.commit()

    r = await list_registered_income(user_id=user_id)
    # Only the CRC income is summed; the USD one is listed but not counted.
    assert r["monthly_income"] == "500000.00"
    assert len(r["incomes"]) == 2
    usd = next(i for i in r["incomes"] if i["currency"] == "USD")
    assert usd["counted_in_monthly"] is False
    assert usd["monthly_equivalent"] is None


@pytest.mark.asyncio
async def test_aguinaldo_excluded_from_monthly(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, "income", session)
    salary = _income(user_id, amount=Decimal("800000"))
    session.add(salary)
    await session.flush()
    session.add(
        RecurringIncome(
            user_id=user_id,
            name="Aguinaldo",
            income_type="aguinaldo",
            amount=Decimal("800000"),
            currency="CRC",
            frequency="annual",
            next_payment_date=date(2026, 12, 15),
            base_salary_link_id=salary.id,
        )
    )
    await session.commit()

    r = await list_registered_income(user_id=user_id)
    assert r["monthly_income"] == "800000.00"  # aguinaldo NOT amortized in
    agui = next(i for i in r["incomes"] if i["income_type"] == "aguinaldo")
    assert agui["counted_in_monthly"] is False


@pytest.mark.asyncio
async def test_no_income_returns_null(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, "income", session)

    r = await list_registered_income(user_id=user_id)
    assert r["monthly_income"] is None
    assert r["incomes"] == []


@pytest.mark.asyncio
async def test_savings_capacity_exposes_debt_to_income_ratio(
    db_with_user, monkeypatch
):
    session, user_id = db_with_user
    _patch_session(monkeypatch, "affordability", session)
    session.add(_income(user_id, amount=Decimal("800000")))
    # A budget so the cashflow isn't gated; a debt with a 200,000 cuota.
    session.add(
        Envelope(
            user_id=user_id,
            name="Gastos",
            envelope_class="needs",
            limit_amount=Decimal("300000"),
            currency="CRC",
        )
    )
    session.add(
        Debt(
            user_id=user_id,
            name="Préstamo",
            debt_type="loan",
            original_amount=Decimal("3000000"),
            current_balance=Decimal("2000000"),
            interest_rate=Decimal("0.18"),
            minimum_payment=Decimal("200000"),
            payment_due_day=1,
        )
    )
    await session.commit()

    r = await get_savings_capacity(user_id=user_id)
    assert r["monthly_income"] == "800000.00"
    assert r["monthly_debt_payments"] == "200000.00"
    # 200,000 / 800,000 = 0.25 — deterministic, LLM only narrates it.
    assert r["debt_to_income_ratio"] == 0.25
