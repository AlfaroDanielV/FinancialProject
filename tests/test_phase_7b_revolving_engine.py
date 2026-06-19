"""Phase 7b B4 — pure revolving-credit engine (app/domain/credit).

No DB, no LLM — exact-reconciliation math tests:
- compute_minimum: pct vs floor takeover, balance cap, zero balance.
- project_minimum_only: pays off with a floor; reconciles
  total_paid == balance + total_interest; never-payoff when the minimum
  doesn't cover the month's interest (the CR "minimum trap").
- project_fixed_payment: hand-checked 2-month case; payment ≤ interest →
  never_pays_off.
- payment_for_months: French formula golden value; zero-rate degenerate.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.credit import (
    compute_minimum,
    payment_for_months,
    project_fixed_payment,
    project_minimum_only,
)


def D(value: str) -> Decimal:
    return Decimal(value)


# ── compute_minimum ───────────────────────────────────────────────────────────


def test_minimum_pct_dominates_above_floor():
    # 2.5% of 1,000,000 = 25,000 > floor 5,000.
    assert compute_minimum(
        D("1000000"), minimum_pct=D("0.025"), minimum_floor=D("5000")
    ) == D("25000.00")


def test_minimum_floor_takes_over_on_small_balances():
    # 2.5% of 100,000 = 2,500 < floor 5,000 → floor wins.
    assert compute_minimum(
        D("100000"), minimum_pct=D("0.025"), minimum_floor=D("5000")
    ) == D("5000.00")


def test_minimum_never_exceeds_balance():
    assert compute_minimum(
        D("3000"), minimum_pct=D("0.025"), minimum_floor=D("5000")
    ) == D("3000.00")


def test_minimum_zero_balance_is_zero():
    assert compute_minimum(
        D("0"), minimum_pct=D("0.025"), minimum_floor=D("5000")
    ) == D("0.00")


def test_minimum_rejects_bad_pct():
    with pytest.raises(ValueError):
        compute_minimum(D("1000"), minimum_pct=D("0"))


# ── project_minimum_only ──────────────────────────────────────────────────────


def test_minimum_only_pays_off_and_reconciles():
    """Floor-driven payoff: low rate so the 5% minimum + floor retire the
    debt; the cents must reconcile exactly."""
    projection = project_minimum_only(
        D("100000"),
        annual_rate=D("0.24"),  # 2%/month
        minimum_pct=D("0.05"),
        minimum_floor=D("10000"),
    )
    assert projection.never_pays_off is False
    assert projection.months is not None and projection.months > 0
    # Exact reconciliation: everything paid = principal + interest.
    assert projection.total_paid == D("100000") + projection.total_interest
    assert projection.rows[-1].ending_balance == D("0.00")


def test_minimum_trap_never_pays_off():
    """CR reality check: 45% APR (3.75%/month interest) with a 2.5% minimum
    and no floor — the minimum doesn't cover the interest. The honest verdict
    is the feature."""
    projection = project_minimum_only(
        D("500000"),
        annual_rate=D("0.45"),
        minimum_pct=D("0.025"),
        minimum_floor=None,
    )
    assert projection.never_pays_off is True
    assert projection.months is None
    assert projection.first_month_interest == D("18750.00")  # 500000 × 0.0375


def test_minimum_boundary_pct_equals_monthly_rate_is_still_never():
    """pct == rate/12 decays so slowly it blows the 600-month cap → never."""
    projection = project_minimum_only(
        D("500000"),
        annual_rate=D("0.45"),
        minimum_pct=D("0.0375"),
        minimum_floor=None,
    )
    assert projection.never_pays_off is True


def test_minimum_only_zero_balance_short_circuits():
    projection = project_minimum_only(
        D("0"), annual_rate=D("0.45"), minimum_pct=D("0.025")
    )
    assert projection.months == 0
    assert projection.total_paid == D("0.00")
    assert projection.never_pays_off is False


# ── project_fixed_payment ─────────────────────────────────────────────────────


def test_fixed_payment_two_month_hand_check():
    """100,000 at 12% (1%/month), paying 60,000:
    m1: interest 1,000 → balance 41,000; m2: interest 410 → pays 41,410.
    Total interest 1,410; total paid 101,410."""
    projection = project_fixed_payment(
        D("100000"), annual_rate=D("0.12"), payment=D("60000")
    )
    assert projection.months == 2
    assert projection.total_interest == D("1410.00")
    assert projection.total_paid == D("101410.00")
    assert projection.rows[1].payment == D("41410.00")


def test_fixed_payment_below_interest_never_pays_off():
    # 500,000 at 45% → first month interest 15,625 > payment 10,000.
    projection = project_fixed_payment(
        D("500000"), annual_rate=D("0.45"), payment=D("10000")
    )
    assert projection.never_pays_off is True
    assert projection.months is None


# ── payment_for_months ────────────────────────────────────────────────────────


def test_payment_for_months_golden_french_value():
    """1,000,000 at 45% annual over 12 months → PMT = 105,012.30 (French
    formula at 3.75%/month: 0.0375 / (1 − 1.0375⁻¹²) ≈ 0.1050123)."""
    payment = payment_for_months(
        D("1000000"), annual_rate=D("0.45"), months=12
    )
    assert payment == D("105012.30")
    # Sanity: that payment must actually pay it off in 12 months.
    projection = project_fixed_payment(
        D("1000000"), annual_rate=D("0.45"), payment=payment
    )
    assert projection.months == 12


def test_payment_for_months_zero_rate_divides_evenly():
    assert payment_for_months(D("120000"), annual_rate=D("0"), months=12) == D(
        "10000.00"
    )
