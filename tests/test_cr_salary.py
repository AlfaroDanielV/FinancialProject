"""Deterministic CR net-salary calculator — unit + golden tests.

All figures are hand-computed from the 2026 tables (Decreto 45333-H + CCSS
obrero 10.83%). The calculator is pure, so these are plain (non-async) tests.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.domain.payroll import (
    UnconfiguredYearError,
    compute_net_salary,
    configured_years,
)


# ── CCSS + sub-threshold ISR ────────────────────────────────────────────────


def test_below_first_tramo_has_zero_isr_but_ccss_applies():
    # 800,000 < 918,000 → no ISR, but CCSS obrero still deducted.
    r = compute_net_salary(gross_monthly=800_000, year=2026)
    assert r.ccss.sem == 44_000          # 5.50%
    assert r.ccss.ivm == 34_640          # 4.33%
    assert r.ccss.banco_popular == 8_000  # 1.00%
    assert r.ccss.total == 86_640         # 10.83%
    assert r.isr.tax == 0
    assert r.isr.tramos == ()             # nothing taxable
    assert r.net_monthly == 713_360


# ── marginal (not flat) across tramos 2–5 ───────────────────────────────────


def test_tramo_2_marginal():
    r = compute_net_salary(gross_monthly=1_000_000, year=2026)
    # only the 82,000 above 918,000 is taxed at 10%.
    assert r.isr.tax == 8_200
    assert r.net_monthly == 883_500  # 1,000,000 − 108,300 − 8,200


def test_tramo_3_marginal():
    r = compute_net_salary(gross_monthly=1_500_000, year=2026)
    # 42,900 (tramo 2) + 22,950 (153,000 @15%) = 65,850.
    assert r.isr.tax == 65_850
    assert r.ccss.total == 162_450
    assert r.net_monthly == 1_271_700


def test_tramo_4_marginal():
    r = compute_net_salary(gross_monthly=3_000_000, year=2026)
    # 42,900 + 152,550 + 127,200 (636,000 @20%) = 322,650.
    assert r.isr.tax == 322_650
    assert r.net_monthly == 2_352_450


def test_tramo_5_marginal_not_flat():
    r = compute_net_salary(gross_monthly=6_000_000, year=2026)
    # 42,900 + 152,550 + 472,600 + 318,250 = 986,300.
    assert r.isr.tax == 986_300
    # Marginal, not a flat 25% on the whole salary.
    assert r.isr.tax < int(6_000_000 * 0.25)
    # The top tramo only taxes the slice above 4,727,000 (1,273,000 @25%).
    top = next(t for t in r.isr.tramos if t.tramo == 5)
    assert top.taxable_in_tramo == 1_273_000
    assert top.tax_in_tramo == 318_250
    assert r.net_monthly == 4_363_900


# ── boundary ────────────────────────────────────────────────────────────────


def test_exact_tramo_boundary_taxes_only_lower_tramo():
    # Exactly at the tramo 2/3 boundary: tramo 3 contributes nothing.
    r = compute_net_salary(gross_monthly=1_347_000, year=2026)
    assert r.isr.tax == 42_900            # 429,000 @10%, tramo 3 = 0
    assert all(t.tramo != 3 for t in r.isr.tramos)


# ── créditos fiscales ───────────────────────────────────────────────────────


def test_creditos_reduce_tax():
    base = compute_net_salary(gross_monthly=1_500_000, year=2026)
    with_credits = compute_net_salary(
        gross_monthly=1_500_000, year=2026, hijos=2, conyuge=True
    )
    # 2×1,710 + 2,590 = 6,010 credited.
    assert with_credits.isr.creditos_aplicados == 6_010
    assert with_credits.isr.tax == base.isr.tax - 6_010
    assert with_credits.net_monthly == base.net_monthly + 6_010


def test_creditos_floor_at_zero_never_negative():
    # tax_before on 1,000,000 is only 8,200; 10 hijos = 17,100 in credits.
    r = compute_net_salary(gross_monthly=1_000_000, year=2026, hijos=10)
    assert r.isr.tax == 0                    # floored, never negative
    assert r.isr.creditos_aplicados == 8_200  # only what was used
    assert r.net_monthly == 891_700           # 1,000,000 − 108,300 − 0


# ── isr_base_mode divergence ────────────────────────────────────────────────


def test_gross_vs_gross_minus_ccss_diverge():
    gross_mode = compute_net_salary(
        gross_monthly=1_500_000, year=2026, isr_base_mode="gross"
    )
    net_ccss_mode = compute_net_salary(
        gross_monthly=1_500_000, year=2026, isr_base_mode="gross_minus_ccss"
    )
    assert gross_mode.isr.tax == 65_850
    # base = 1,500,000 − 162,450 = 1,337,550 → only 419,550 @10% = 41,955.
    assert net_ccss_mode.isr.tax == 41_955
    assert net_ccss_mode.isr.tax < gross_mode.isr.tax
    assert net_ccss_mode.isr.base == 1_337_550
    assert gross_mode.isr.base == 1_500_000


# ── golden cross-check ──────────────────────────────────────────────────────


def test_golden_1_5m_gross_mode():
    """Golden reference: ₡1.500.000 gross, 2026, no dependents, gross base.

    Computed on the GROSS base per Decreto 45333-H ("Tramos de renta (salario
    bruto)"). Local calculators like elempleo net CCSS out of the base first and
    will report a slightly higher net (≈₡1.295.595, our ``gross_minus_ccss``
    mode) — the divergence is entirely attributable to ``isr_base_mode``.
    """
    r = compute_net_salary(gross_monthly=1_500_000, year=2026)
    assert r.ccss.total == 162_450
    assert r.isr.tax == 65_850
    assert r.net_monthly == 1_271_700
    assert r.effective_rate == 0.1522
    # The documented elempleo-style figure is the gross_minus_ccss net.
    alt = compute_net_salary(
        gross_monthly=1_500_000, year=2026, isr_base_mode="gross_minus_ccss"
    )
    assert alt.net_monthly == 1_295_595


# ── solidarista (optional param — always available) ─────────────────────────


def test_solidarista_reduces_take_home():
    base = compute_net_salary(gross_monthly=1_500_000, year=2026)
    with_sol = compute_net_salary(
        gross_monthly=1_500_000, year=2026, solidarista_pct=5
    )
    assert with_sol.solidarista == 75_000        # 5% of gross
    assert with_sol.net_monthly == base.net_monthly - 75_000
    assert with_sol.net_monthly == 1_196_700
    # solidarista counts toward total deductions in the effective rate.
    assert with_sol.effective_rate == 0.2022


# ── rounding reconciliation ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "gross,sol", [(800_000, 0), (1_234_567, 0), (1_500_000, 5), (6_000_000, 3)]
)
def test_components_reconcile_to_net(gross, sol):
    r = compute_net_salary(gross_monthly=gross, year=2026, solidarista_pct=sol)
    # Components sum to net exactly (spec allows ±1; we guarantee 0).
    assert r.net_monthly + r.ccss.total + r.isr.tax + r.solidarista == gross
    # CCSS sub-components reconcile to the CCSS total.
    assert r.ccss.sem + r.ccss.ivm + r.ccss.banco_popular == r.ccss.total


# ── year handling ───────────────────────────────────────────────────────────


def test_unconfigured_year_raises():
    with pytest.raises(UnconfiguredYearError):
        compute_net_salary(gross_monthly=1_000_000, year=2099)


def test_default_year_is_current_calendar_year():
    yr = date.today().year
    if yr in configured_years():
        r = compute_net_salary(gross_monthly=1_000_000)
        assert r.year == yr
    else:
        # Unconfigured current year must fail loudly, not silently use a stale set.
        with pytest.raises(UnconfiguredYearError):
            compute_net_salary(gross_monthly=1_000_000)


# ── input validation ────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [0, -1, -100_000])
def test_non_positive_gross_raises(bad):
    with pytest.raises(ValueError):
        compute_net_salary(gross_monthly=bad, year=2026)


def test_negative_hijos_raises():
    with pytest.raises(ValueError):
        compute_net_salary(gross_monthly=1_000_000, year=2026, hijos=-1)


def test_bad_isr_base_mode_raises():
    with pytest.raises(ValueError):
        compute_net_salary(
            gross_monthly=1_000_000, year=2026, isr_base_mode="weird"  # type: ignore[arg-type]
        )
