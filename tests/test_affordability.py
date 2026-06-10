"""Phase 7 — deterministic affordability engine + read-only query tool.

The verdict is now judged against the envelope-aware SURPLUS (income − committed
envelopes), gated when the cashflow isn't trustworthy
([[Decision - Unified Monthly Cashflow]]). The math is pure (no DB, no LLM) so
most cases drive ``assess_affordability`` over a ``build_monthly_cashflow``
fixture directly. A few DB-backed cases prove ``compute_monthly_cashflow`` +
the query tools against real seeded income / bills / debt / envelopes.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from api.models.debt import Debt
from api.models.envelope import Envelope
from api.models.recurring_bill import RecurringBill
from api.models.recurring_income import RecurringIncome
from api.services.finance.affordability import SAFETY_MARGIN, assess_affordability
from api.services.finance.cashflow import build_monthly_cashflow, UnattachedObligation
from app.queries.tools.affordability import assess_purchase, get_savings_capacity


def _d(value: str) -> Decimal:
    return Decimal(value)


def _cf(
    income,
    fixed: str,
    debt: str,
    envelopes: str,
    *,
    has_budget: bool = True,
    savings: str = "0",
    excl_var: int = 0,
    excl_custom: int = 0,
    unattached: tuple = (),
):
    """Build a MonthlyCashflow fixture. Under the per-item gate (B3) the
    debt/bills passed here are transparency-only; pass ``unattached`` to simulate
    obligations with no envelope (which trigger under_coverage)."""
    return build_monthly_cashflow(
        monthly_income=(_d(income) if income is not None else None),
        debt_payments=_d(debt),
        recurring_bills=_d(fixed),
        envelope_allocations=_d(envelopes),
        has_budget=has_budget,
        savings_allocations=_d(savings),
        excluded_variable_bills=excl_var,
        excluded_custom_bills=excl_custom,
        unattached_obligations=unattached,
    )


# --------------------------------------------------------------------------- #
# Pure engine — verdict over the gated surplus                                 #
# --------------------------------------------------------------------------- #

def test_feasible_over_timeline():
    # income 1,000,000 − committed envelopes 500,000 = 500,000 surplus.
    # safe = 80% = 400,000. Target 1,200,000 over 6 months → needed 200,000.
    cf = _cf("1000000", "300000", "200000", "500000")  # envelopes cover obligations
    r = assess_affordability(cf, desired_amount=_d("1200000"), timeline_months=6)
    assert r.feasible is True
    assert r.gate_reason is None
    assert r.surplus == _d("500000.00")
    assert r.safe_surplus == _d("400000.00")
    assert r.monthly_needed == _d("200000.00")
    assert r.shortfall == _d("0.00")
    assert r.min_timeline_months_feasible == 3
    assert r.max_amount_feasible_in_timeline == _d("2400000.00")


def test_infeasible_reports_shortfall_and_alternatives():
    # Same surplus 500,000 (safe 400,000); ask 6,000,000 over 6 months → 1,000,000.
    cf = _cf("1000000", "300000", "200000", "500000")
    r = assess_affordability(cf, desired_amount=_d("6000000"), timeline_months=6)
    assert r.feasible is False
    assert r.shortfall == _d("600000.00")
    assert r.min_timeline_months_feasible == 15
    assert r.max_amount_feasible_in_timeline == _d("2400000.00")


def test_immediate_purchase_defaults_to_one_month():
    cf = _cf("1000000", "300000", "200000", "500000")  # surplus 500k, safe 400k
    feasible = assess_affordability(cf, desired_amount=_d("300000"))  # ≤ 400,000
    assert feasible.timeline_months == 1
    assert feasible.feasible is True

    over = assess_affordability(cf, desired_amount=_d("500000"))  # > 400,000
    assert over.feasible is False


def test_deficit_has_no_feasible_timeline():
    # income 500,000 − committed envelopes 600,000 = −100,000 surplus (a reliable
    # deficit: envelopes cover the 600,000 of obligations exactly).
    cf = _cf("500000", "400000", "200000", "600000")
    r = assess_affordability(cf, desired_amount=_d("100000"), timeline_months=3)
    assert r.feasible is False
    assert r.surplus == _d("-100000.00")
    # safe is negative → no finite horizon makes it feasible.
    assert r.min_timeline_months_feasible is None
    assert r.max_amount_feasible_in_timeline == _d("0")


def test_no_income_gates_the_verdict():
    cf = _cf(None, "300000", "0", "0", has_budget=False)
    r = assess_affordability(cf, desired_amount=_d("100000"), timeline_months=4)
    assert r.feasible is None
    assert r.gate_reason == "no_income"
    assert r.surplus is None
    assert r.safe_surplus is None
    assert any("ingresos recurrentes" in n for n in r.notes)


def test_no_budget_gates_the_verdict():
    # Income known but no envelopes → committed would be 0 and surplus collapses
    # to the old inflated figure. Withhold the verdict.
    cf = _cf("800000", "150000", "100000", "0", has_budget=False)
    r = assess_affordability(cf, desired_amount=_d("100000"))
    assert r.feasible is None
    assert r.gate_reason == "no_budget"
    assert any("sobres" in n for n in r.notes)


def test_under_coverage_gates_the_verdict():
    # An active bill with no envelope → committed (= envelopes) understates
    # reality → surplus inflated → gate, naming the unattached item.
    cf = _cf(
        "800000", "150000", "100000", "300000", has_budget=True,
        unattached=(UnattachedObligation("ICE", _d("150000"), "bill"),),
    )
    r = assess_affordability(cf, desired_amount=_d("100000"))
    assert r.feasible is None
    assert r.gate_reason == "under_coverage"
    assert any("ICE" in n for n in r.notes)  # the copy names what to attach


def test_savings_allocations_ride_along_in_the_result():
    cf = _cf("1000000", "300000", "200000", "600000", savings="200000")
    r = assess_affordability(cf, desired_amount=_d("100000"))
    # committed = all envelopes (600k); savings (200k of them) surfaced separately.
    assert r.committed_outflows == _d("600000.00")
    assert r.savings_allocations == _d("200000.00")


def test_excluded_bills_surface_as_notes():
    cf = _cf("1000000", "300000", "0", "300000", excl_var=2, excl_custom=1)
    r = assess_affordability(cf, desired_amount=_d("100000"), timeline_months=1)
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


async def _seed_finances(session, user_id, *, with_budget=True):
    """Income 800,000, bill 150,000, debt 100,000. With a 250,000 'needs'
    envelope the bill + debt are ATTACHED to it (per-item coverage, B3), so the
    cashflow is reliable; surplus = income − envelopes = 550,000."""
    env_id = None
    if with_budget:
        env = Envelope(
            user_id=user_id, name="Gastos", envelope_class="needs",
            limit_amount=Decimal("250000"), currency="CRC",
        )
        session.add(env)
        await session.flush()
        env_id = env.id
    rows = [
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
            envelope_id=env_id,
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
            envelope_id=env_id,
        ),
    ]
    session.add_all(rows)
    await session.commit()


@pytest.mark.asyncio
async def test_assess_purchase_tool_uses_real_data(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _seed_finances(session, user_id)

    # surplus 800,000 − 250,000 committed = 550,000. safe 440,000. Target
    # 880,000 over 4 months → needed 220,000 ≤ 440,000 → feasible.
    result = await assess_purchase(amount=880000, timeline_months=4, user_id=user_id)

    assert result["currency"] == "CRC"
    assert result["monthly_income"] == "800000.00"
    assert result["monthly_fixed_expenses"] == "150000.00"
    assert result["monthly_debt_payments"] == "100000.00"
    assert result["committed_outflows"] == "250000.00"
    assert result["surplus"] == "550000.00"
    assert result["safe_surplus"] == "440000.00"
    assert result["monthly_needed"] == "220000.00"
    assert result["feasible"] is True
    assert result["gate_reason"] is None
    assert result["shortfall"] == "0.00"


@pytest.mark.asyncio
async def test_assess_purchase_tool_no_budget_gates(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _seed_finances(session, user_id, with_budget=False)  # income but no envelopes

    result = await assess_purchase(amount=100000, user_id=user_id)
    assert result["feasible"] is None
    assert result["gate_reason"] == "no_budget"
    # committed = 0 (no envelopes) → surplus collapses to the full income: the
    # inflated figure the gate exists to suppress. Present, but never surfaced.
    assert result["surplus"] == "800000.00"
    assert result["committed_outflows"] == "0.00"


@pytest.mark.asyncio
async def test_assess_purchase_tool_no_income_is_unknown(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    # No recurring income seeded → no_income gate wins (most fundamental).

    result = await assess_purchase(amount=100000, user_id=user_id)
    assert result["feasible"] is None
    assert result["gate_reason"] == "no_income"
    assert result["surplus"] is None
    assert any("ingresos recurrentes" in n for n in result["notes"])


@pytest.mark.asyncio
async def test_savings_capacity_reports_envelope_aware_surplus(db_with_user, monkeypatch):
    """The surplus nets out the committed budget, not just debt — surplus 550,000
    (income 800,000 − committed 250,000). The per-debt breakdown stays visible."""
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    await _seed_finances(session, user_id)

    result = await get_savings_capacity(user_id=user_id)
    assert result["monthly_income"] == "800000.00"
    assert result["monthly_debt_payments"] == "100000.00"
    assert result["committed_outflows"] == "250000.00"
    assert result["surplus"] == "550000.00"
    assert result["safe_surplus"] == "440000.00"
    assert result["gate_reason"] is None
    assert len(result["debts"]) == 1
    assert result["debts"][0]["name"] == "Préstamo personal"
    assert result["debts"][0]["monthly_payment"] == "100000.00"


@pytest.mark.asyncio
async def test_savings_capacity_no_income_is_unknown(db_with_user, monkeypatch):
    session, user_id = db_with_user
    _patch_session(monkeypatch, session)
    # No income seeded → honest unknown, gated, not a fabricated zero.

    result = await get_savings_capacity(user_id=user_id)
    assert result["surplus"] is None
    assert result["safe_surplus"] is None
    assert result["gate_reason"] == "no_income"
    assert any("ingresos recurrentes" in n for n in result["notes"])
