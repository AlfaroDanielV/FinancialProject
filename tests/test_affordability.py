"""Phase 7 — deterministic affordability engine + read-only query tool.

The math is pure (no DB, no LLM) so most cases drive ``assess_affordability``
directly. A few DB-backed cases prove the gather helper and the
``assess_purchase`` query tool against real seeded income / bills / debt.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from api.models.debt import Debt
from api.models.recurring_bill import RecurringBill
from api.models.recurring_income import RecurringIncome
from api.services.finance.affordability import (
    SAFETY_MARGIN,
    assess_affordability,
)
from app.queries.tools.affordability import assess_purchase, get_savings_capacity


# --------------------------------------------------------------------------- #
# Pure engine                                                                  #
# --------------------------------------------------------------------------- #

def _d(value: str) -> Decimal:
    return Decimal(value)


def test_feasible_over_timeline():
    # income 1,000,000 − fixed 300,000 − debt 200,000 = 500,000 disposable.
    # safe = 80% = 400,000. Target 1,200,000 over 6 months → needed 200,000.
    r = assess_affordability(
        monthly_income=_d("1000000"),
        monthly_fixed_expenses=_d("300000"),
        monthly_debt_payments=_d("200000"),
        desired_amount=_d("1200000"),
        timeline_months=6,
    )
    assert r.feasible is True
    assert r.monthly_disposable == _d("500000.00")
    assert r.safe_monthly_disposable == _d("400000.00")
    assert r.monthly_needed == _d("200000.00")
    assert r.shortfall == _d("0.00")
    # ceil(1,200,000 / 400,000) = 3; could even be done faster than requested.
    assert r.min_timeline_months_feasible == 3
    # most you could save in the requested 6 months at the safe rate.
    assert r.max_amount_feasible_in_timeline == _d("2400000.00")


def test_infeasible_reports_shortfall_and_alternatives():
    # Same disposable; ask for 6,000,000 over 6 months → needed 1,000,000.
    r = assess_affordability(
        monthly_income=_d("1000000"),
        monthly_fixed_expenses=_d("300000"),
        monthly_debt_payments=_d("200000"),
        desired_amount=_d("6000000"),
        timeline_months=6,
    )
    assert r.feasible is False
    # needed 1,000,000 − safe 400,000.
    assert r.shortfall == _d("600000.00")
    # ceil(6,000,000 / 400,000) = 15 months to make it fit.
    assert r.min_timeline_months_feasible == 15
    assert r.max_amount_feasible_in_timeline == _d("2400000.00")


def test_immediate_purchase_defaults_to_one_month():
    # No timeline → months=1 → needed == full amount, tested against safe.
    feasible = assess_affordability(
        monthly_income=_d("1000000"),
        monthly_fixed_expenses=_d("300000"),
        monthly_debt_payments=_d("200000"),
        desired_amount=_d("300000"),  # <= safe 400,000
    )
    assert feasible.timeline_months == 1
    assert feasible.feasible is True

    over = assess_affordability(
        monthly_income=_d("1000000"),
        monthly_fixed_expenses=_d("300000"),
        monthly_debt_payments=_d("200000"),
        desired_amount=_d("500000"),  # > safe 400,000
    )
    assert over.feasible is False


def test_over_committed_has_no_feasible_timeline():
    # income 500,000 − fixed 400,000 − debt 200,000 = −100,000 disposable.
    r = assess_affordability(
        monthly_income=_d("500000"),
        monthly_fixed_expenses=_d("400000"),
        monthly_debt_payments=_d("200000"),
        desired_amount=_d("100000"),
        timeline_months=3,
    )
    assert r.feasible is False
    assert r.monthly_disposable == _d("-100000.00")
    # safe is negative → no finite horizon makes it feasible.
    assert r.min_timeline_months_feasible is None
    assert r.max_amount_feasible_in_timeline == _d("0")


def test_no_income_is_unknown_not_fabricated():
    r = assess_affordability(
        monthly_income=None,
        monthly_fixed_expenses=_d("300000"),
        monthly_debt_payments=_d("0"),
        desired_amount=_d("100000"),
        timeline_months=4,
    )
    assert r.feasible is None
    assert r.monthly_disposable is None
    assert r.safe_monthly_disposable is None
    assert any("ingresos recurrentes" in n for n in r.notes)


def test_excluded_bills_surface_as_notes():
    r = assess_affordability(
        monthly_income=_d("1000000"),
        monthly_fixed_expenses=_d("300000"),
        monthly_debt_payments=_d("0"),
        desired_amount=_d("100000"),
        timeline_months=1,
        excluded_variable_bills=2,
        excluded_custom_bills=1,
    )
    assert any("variable" in n for n in r.notes)
    assert any("personalizada" in n for n in r.notes)


def test_safety_margin_is_eighty_percent():
    assert SAFETY_MARGIN == Decimal("0.80")


# --------------------------------------------------------------------------- #
# DB-backed gather + query tool                                                #
# --------------------------------------------------------------------------- #

class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _patch_session(monkeypatch, session):
    monkeypatch.setattr(
        "app.queries.tools.affordability.AsyncSessionLocal",
        lambda: _SessionContext(session),
    )


async def _seed_finances(session, user_id):
    session.add_all(
        [
            RecurringIncome(
                user_id=user_id,
                name="Salario",
                income_type="salary",
                amount=Decimal("800000"),
                currency="CRC",
                frequency="monthly",
                next_payment_date=date.today(),
            ),
            RecurringBill(
                user_id=user_id,
                name="Internet",
                category="servicios",
                amount_expected=Decimal("150000"),
                currency="CRC",
                frequency="monthly",
                start_date=date.today(),
            ),
            Debt(
                user_id=user_id,
                name="Préstamo personal",
                debt_type="loan",
                original_amount=Decimal("3000000"),
                current_balance=Decimal("2000000"),
                interest_rate=Decimal("0.18"),
                minimum_payment=Decimal("100000"),
                payment_due_day=1,
            ),
        ]
    )
    await session.commit()


@pytest.mark.asyncio
async def test_assess_purchase_tool_uses_real_data(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _seed_finances(session, user_id)

    # income 800,000 − fixed 150,000 − debt 100,000 = 550,000 disposable.
    # safe = 440,000. Target 880,000 over 4 months → needed 220,000 ≤ 440,000.
    result = await assess_purchase(amount=880000, timeline_months=4, user_id=user_id)

    assert result["currency"] == "CRC"
    assert result["monthly_income"] == "800000.00"
    assert result["monthly_fixed_expenses"] == "150000.00"
    assert result["monthly_debt_payments"] == "100000.00"
    assert result["monthly_disposable"] == "550000.00"
    assert result["safe_monthly_disposable"] == "440000.00"
    assert result["monthly_needed"] == "220000.00"
    assert result["feasible"] is True
    assert result["shortfall"] == "0.00"


@pytest.mark.asyncio
async def test_assess_purchase_tool_no_income_is_unknown(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    # No recurring income seeded → engine returns feasible=None honestly.

    result = await assess_purchase(amount=100000, user_id=user_id)
    assert result["feasible"] is None
    assert result["monthly_disposable"] is None
    assert any("ingresos recurrentes" in n for n in result["notes"])


@pytest.mark.asyncio
async def test_savings_capacity_subtracts_debt_payments(db_with_user, monkeypatch):
    """General savings planning must net out debt payments — income 800,000 −
    fixed 150,000 − debt 100,000 = 550,000 disposable; safe = 440,000. The
    per-debt breakdown surfaces the obligation so the advisor can mention it."""
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _seed_finances(session, user_id)

    result = await get_savings_capacity(user_id=user_id)
    assert result["monthly_income"] == "800000.00"
    assert result["monthly_fixed_expenses"] == "150000.00"
    assert result["monthly_debt_payments"] == "100000.00"
    assert result["monthly_disposable"] == "550000.00"
    assert result["safe_monthly_disposable"] == "440000.00"
    # Debt is visible per-loan, not just folded into the total.
    assert len(result["debts"]) == 1
    assert result["debts"][0]["name"] == "Préstamo personal"
    assert result["debts"][0]["monthly_payment"] == "100000.00"


@pytest.mark.asyncio
async def test_savings_capacity_no_income_is_unknown(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    # No income seeded → disposable is an honest unknown, not a fabricated zero.

    result = await get_savings_capacity(user_id=user_id)
    assert result["monthly_disposable"] is None
    assert result["safe_monthly_disposable"] is None
    assert any("ingresos recurrentes" in n for n in result["notes"])
