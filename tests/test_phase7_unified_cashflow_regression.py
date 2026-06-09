"""Phase 7 — unified cashflow regression (Decision - Unified Monthly Cashflow).

The acceptance scenario for the whole refactor: the ₡1M phone. After unifying
every surface on the one envelope-aware source of truth, the affordability
verdict, the "cuánto me sobra" surplus, and the savings-plan horizon must all be
derived from the SAME surplus — they can no longer disagree. Plus: the three
gates (no_income / no_budget / under_coverage) each fire a DISTINCT, deterministic
guidance copy (a different user action each).
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
from api.services.finance.affordability import assess_affordability, gate_guidance
from api.services.finance.cashflow import build_monthly_cashflow, compute_monthly_cashflow
from app.queries.tools.affordability import assess_purchase, get_savings_capacity


def _d(value: str) -> Decimal:
    return Decimal(value)


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return None


# --------------------------------------------------------------------------- #
# The ₡1M phone — one surplus underlies every answer                          #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_phone_1m_verdict_surplus_and_plan_are_consistent(db_with_user, monkeypatch):
    session, user_id = db_with_user
    user = await session.get(User, user_id)
    monkeypatch.setattr(
        "app.queries.tools.affordability.AsyncSessionLocal",
        lambda: _SessionContext(session),
    )
    # income 900,000; bills 100,000 + debt 100,000 = 200,000 obligations; budget
    # 600,000 needs + 200,000 savings = 800,000 committed (covers obligations).
    # surplus = 900,000 − 800,000 = 100,000. safe = 80,000.
    session.add_all(
        [
            RecurringIncome(
                user_id=user_id, name="Salario", income_type="salary",
                amount=_d("900000"), currency="CRC", frequency="monthly",
                next_payment_date=date.today(),
            ),
            RecurringBill(
                user_id=user_id, name="Internet", category="servicios",
                amount_expected=_d("100000"), currency="CRC", frequency="monthly",
                start_date=date.today(),
            ),
            Debt(
                user_id=user_id, name="Préstamo", debt_type="loan",
                original_amount=_d("2000000"), current_balance=_d("1500000"),
                interest_rate=_d("0.18"), minimum_payment=_d("100000"), payment_due_day=1,
            ),
            Envelope(
                user_id=user_id, name="Gastos", envelope_class="needs",
                limit_amount=_d("600000"), currency="CRC",
            ),
            Envelope(
                user_id=user_id, name="Ahorro", envelope_class="savings",
                limit_amount=_d("200000"), currency="CRC",
            ),
        ]
    )
    await session.commit()

    # 1) "¿puedo comprar el teléfono de ₡1.000.000?" (this month)
    purchase = await assess_purchase(amount=1000000, user_id=user_id)
    # 2) "¿cuánto me sobra al mes?"
    savings = await get_savings_capacity(user_id=user_id)
    # 3) the savings-plan horizon, straight off the single source.
    cashflow = await compute_monthly_cashflow(session, user=user)

    # The same surplus / committed underlies BOTH tools — no divergence possible.
    assert purchase["surplus"] == savings["surplus"] == "100000.00"
    assert purchase["committed_outflows"] == savings["committed_outflows"] == "800000.00"
    assert purchase["safe_surplus"] == savings["safe_surplus"] == "80000.00"
    assert purchase["savings_allocations"] == savings["savings_allocations"] == "200000.00"
    assert purchase["gate_reason"] is None and savings["gate_reason"] is None

    # The phone doesn't fit THIS month (1M > 80k safe) — but the verdict and the
    # surplus cohere: ~13 months at the safe rate, ~10 at the raw surplus. No more
    # "no" + "₡900k sobra" contradiction.
    assert purchase["feasible"] is False
    assert purchase["min_timeline_months_feasible"] == 13  # ceil(1M / 80k)
    assert cashflow.months_to_goal(_d("1000000")) == 10  # ceil(1M / 100k surplus)


# --------------------------------------------------------------------------- #
# The three gates each fire distinct guidance                                 #
# --------------------------------------------------------------------------- #

def _cf(income, bills, debt, envelopes, *, has_budget):
    return build_monthly_cashflow(
        monthly_income=income,
        debt_payments=_d(debt),
        recurring_bills=_d(bills),
        envelope_allocations=_d(envelopes),
        has_budget=has_budget,
    )


def test_three_gates_have_distinct_reason_and_copy():
    no_income = _cf(None, "0", "0", "0", has_budget=False)
    no_budget = _cf(_d("800000"), "150000", "100000", "0", has_budget=False)
    under = _cf(_d("800000"), "150000", "100000", "100000", has_budget=True)

    assert no_income.gate_reason == "no_income"
    assert no_budget.gate_reason == "no_budget"
    assert under.gate_reason == "under_coverage"

    copies = {gate_guidance(cf.gate_reason) for cf in (no_income, no_budget, under)}
    assert len(copies) == 3  # all three are distinct strings

    # Each names its own DISTINCT user action.
    assert "ingreso" in gate_guidance("no_income").lower()
    assert "sobres" in gate_guidance("no_budget").lower()
    assert "cubren" in gate_guidance("under_coverage").lower()

    # And the engine withholds the verdict for all three, tagging the reason.
    for cf in (no_income, no_budget, under):
        r = assess_affordability(cf, desired_amount=_d("100000"))
        assert r.feasible is None
        assert r.gate_reason == cf.gate_reason
