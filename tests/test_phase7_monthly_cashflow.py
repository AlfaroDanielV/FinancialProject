"""Phase 7 — unified monthly cashflow (single source of truth).

The summation + gate logic is pure (no DB, no LLM), so most cases drive
``build_monthly_cashflow`` directly. One DB-backed case proves
``compute_monthly_cashflow`` against seeded income / bills / debt / envelopes,
including that aguinaldo + salario escolar are excluded from monthly income.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from api.models.debt import Debt
from api.models.envelope import Envelope
from api.models.recurring_bill import RecurringBill
from api.models.recurring_income import RecurringIncome
from api.models.user import User
from api.services.finance.cashflow import (
    build_monthly_cashflow,
    compute_monthly_cashflow,
)


def _d(value: str) -> Decimal:
    return Decimal(value)


# --------------------------------------------------------------------------- #
# Pure builder — Model A summation + gates                                     #
# --------------------------------------------------------------------------- #

def test_positive_surplus_is_reliable():
    # income 800,000 − committed (envelopes) 700,000 = 100,000 surplus.
    # envelopes 700,000 ≥ debt 100,000 + bills 150,000 → covers obligations.
    cf = build_monthly_cashflow(
        monthly_income=_d("1000000"),
        debt_payments=_d("100000"),
        recurring_bills=_d("150000"),
        envelope_allocations=_d("250000"),
        has_budget=True,
    )
    # committed = debt 100k + bills 150k + envelopes 250k = 500k → surplus 500k.
    assert cf.committed_outflows == _d("500000.00")
    assert cf.surplus == _d("500000.00")
    assert cf.has_budget is True
    assert cf.income_known is True
    assert cf.reliable is True
    assert cf.gate_reason is None
    assert cf.is_deficit is False


def test_deficit_is_negative_surplus_surfaced_as_such():
    # income 500,000 − committed 600,000 = −100,000 → a reliable, honest deficit.
    cf = build_monthly_cashflow(
        monthly_income=_d("500000"),
        debt_payments=_d("100000"),
        recurring_bills=_d("0"),
        envelope_allocations=_d("500000"),
        has_budget=True,
    )
    assert cf.committed_outflows == _d("600000.00")
    assert cf.surplus == _d("-100000.00")
    assert cf.reliable is True
    assert cf.gate_reason is None
    assert cf.is_deficit is True


def test_no_budget_gates():
    # No active envelopes → committed 0 → surplus would collapse back to the
    # old inflated income figure. Gate it.
    cf = build_monthly_cashflow(
        monthly_income=_d("800000"),
        debt_payments=_d("100000"),
        recurring_bills=_d("150000"),
        envelope_allocations=_d("0"),
        has_budget=False,
    )
    assert cf.has_budget is False
    assert cf.reliable is False
    assert cf.gate_reason == "no_budget"
    # Components still populated for transparency.
    assert cf.debt_payments == _d("100000.00")
    assert cf.recurring_bills == _d("150000.00")


def test_committed_is_debts_plus_bills_plus_envelopes():
    # Debts + recurring bills are counted from their own tables (the user never
    # mirrors them as sobres), envelopes are discretionary on top. Each amount is
    # counted exactly once. income 1,000,000; committed = 300k + 200k + 400k = 900k.
    cf = build_monthly_cashflow(
        monthly_income=_d("1000000"),
        debt_payments=_d("300000"),
        recurring_bills=_d("200000"),
        envelope_allocations=_d("400000"),
        has_budget=True,
    )
    assert cf.committed_outflows == _d("900000.00")  # 300k + 200k + 400k, no dupes
    assert cf.surplus == _d("100000.00")


def test_savings_allocations_is_transparency_only():
    # savings_allocations never changes committed or surplus (sub-decision B):
    # the engine doesn't decide savings money is grabbable. With income 800,000
    # and 700,000 of envelopes (300,000 of which are savings/investing), committed
    # is still 700,000 and surplus 100,000 — savings_allocations just rides along.
    cf = build_monthly_cashflow(
        monthly_income=_d("800000"),
        debt_payments=_d("0"),
        recurring_bills=_d("0"),
        envelope_allocations=_d("700000"),
        has_budget=True,
        savings_allocations=_d("300000"),
    )
    assert cf.committed_outflows == _d("700000.00")
    assert cf.surplus == _d("100000.00")
    assert cf.savings_allocations == _d("300000.00")


def test_no_income_gates_and_is_not_a_deficit():
    # No recurring income on file → honest unknown. Surplus is mathematically
    # negative (−committed) but must NOT read as a confident deficit.
    cf = build_monthly_cashflow(
        monthly_income=None,
        debt_payments=_d("0"),
        recurring_bills=_d("0"),
        envelope_allocations=_d("300000"),
        has_budget=True,
    )
    assert cf.income_known is False
    assert cf.monthly_income == _d("0.00")
    assert cf.reliable is False
    assert cf.gate_reason == "no_income"  # most-fundamental gate wins
    assert cf.is_deficit is False


def test_gate_precedence_no_income_before_no_budget():
    cf = build_monthly_cashflow(
        monthly_income=None,
        debt_payments=_d("0"),
        recurring_bills=_d("0"),
        envelope_allocations=_d("0"),
        has_budget=False,
    )
    assert cf.gate_reason == "no_income"


# --------------------------------------------------------------------------- #
# months_to_goal                                                              #
# --------------------------------------------------------------------------- #

def _cf_with_surplus(income: str, envelopes: str) -> "object":
    return build_monthly_cashflow(
        monthly_income=_d(income),
        debt_payments=_d("0"),
        recurring_bills=_d("0"),
        envelope_allocations=_d(envelopes),
        has_budget=True,
    )


def test_months_to_goal_rounds_up():
    cf = _cf_with_surplus("700000", "600000")  # surplus 100,000
    assert cf.surplus == _d("100000.00")
    assert cf.months_to_goal(_d("1000000")) == 10


def test_months_to_goal_ceils_partial_month():
    cf = _cf_with_surplus("900000", "600000")  # surplus 300,000
    # ceil(1,000,000 / 300,000) = ceil(3.33) = 4.
    assert cf.months_to_goal(_d("1000000")) == 4


def test_months_to_goal_none_on_deficit():
    cf = _cf_with_surplus("500000", "600000")  # surplus −100,000
    assert cf.months_to_goal(_d("1000000")) is None


def test_months_to_goal_none_on_zero_margin():
    cf = _cf_with_surplus("600000", "600000")  # surplus 0
    assert cf.months_to_goal(_d("1000000")) is None


def test_months_to_goal_already_met_is_zero():
    cf = _cf_with_surplus("700000", "600000")
    assert cf.months_to_goal(_d("0")) == 0


# --------------------------------------------------------------------------- #
# DB-backed — compute_monthly_cashflow end-to-end                             #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_compute_monthly_cashflow_excludes_cr_lump_cycles(db_with_user):
    session, user_id = db_with_user
    user = await session.get(User, user_id)

    salary = RecurringIncome(
        user_id=user_id,
        name="Salario",
        income_type="salary",
        amount=_d("800000"),
        currency="CRC",
        frequency="monthly",
        next_payment_date=date.today(),
    )
    session.add(salary)
    await session.flush()  # need salary.id for the CR-cycle FK + CHECK
    session.add_all(
        [
            # Aguinaldo: annual lump = one monthly salary. MUST be excluded from
            # monthly income (would otherwise add 800,000 ÷ 12 of phantom cash).
            RecurringIncome(
                user_id=user_id,
                name="Aguinaldo (Salario)",
                income_type="aguinaldo",
                amount=_d("800000"),
                currency="CRC",
                frequency="annual",
                next_payment_date=date(date.today().year, 12, 15),
                base_salary_link_id=salary.id,
            ),
            RecurringBill(
                user_id=user_id,
                name="Internet",
                category="servicios",
                amount_expected=_d("150000"),
                currency="CRC",
                frequency="monthly",
                start_date=date.today(),
            ),
            Debt(
                user_id=user_id,
                name="Préstamo personal",
                debt_type="loan",
                original_amount=_d("3000000"),
                current_balance=_d("2000000"),
                interest_rate=_d("0.18"),
                minimum_payment=_d("100000"),
                payment_due_day=1,
            ),
            # Discretionary budget: 150,000 needs + 100,000 wants + 100,000
            # savings = 350,000 of root allocations. committed = debt 100k +
            # bills 150k + envelopes 350k = 600,000. The savings envelope counts
            # toward committed (ALL classes, sub-decision B) AND surfaces in
            # savings_allocations.
            Envelope(
                user_id=user_id,
                name="Gastos del mes",
                envelope_class="needs",
                limit_amount=_d("150000"),
                currency="CRC",
            ),
            Envelope(
                user_id=user_id,
                name="Gustos",
                envelope_class="wants",
                limit_amount=_d("100000"),
                currency="CRC",
            ),
            Envelope(
                user_id=user_id,
                name="Ahorro",
                envelope_class="savings",
                limit_amount=_d("100000"),
                currency="CRC",
            ),
        ]
    )
    await session.commit()

    cf = await compute_monthly_cashflow(session, user=user)

    # Aguinaldo excluded → income is the bare monthly salary, not 866,666.67.
    assert cf.monthly_income == _d("800000.00")
    assert cf.debt_payments == _d("100000.00")
    assert cf.recurring_bills == _d("150000.00")
    assert cf.envelope_allocations == _d("350000.00")  # roots only, all classes
    assert cf.committed_outflows == _d("600000.00")  # debt + bills + envelopes
    assert cf.savings_allocations == _d("100000.00")  # transparency: savings root
    assert cf.surplus == _d("200000.00")
    assert cf.has_budget is True
    assert cf.reliable is True
    assert cf.gate_reason is None
    assert cf.months_to_goal(_d("600000")) == 3
