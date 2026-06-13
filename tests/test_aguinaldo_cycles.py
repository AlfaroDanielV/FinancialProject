"""Deterministic CR aguinaldo + salario-escolar proration — golden tests.

Pure rules layer (`app/domain/payroll/cr_cycles`): aguinaldo = gross × days
worked in the Dec1–Nov30 window / window days; salario escolar = 8.33% of gross
earned in the calendar year, also day-prorated. Hand-computed against fixed
2025/2026 windows (both non-leap → 365 days).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.payroll import (
    SALARIO_ESCOLAR_PCT,
    compute_aguinaldo,
    compute_salario_escolar,
)

G = Decimal("1000000")  # monthly gross


# ── aguinaldo ────────────────────────────────────────────────────────────────


def test_aguinaldo_full_window_is_one_month_gross():
    # Hired before the Dec 1 (2025) window start → full window → one month.
    out = compute_aguinaldo(
        monthly_gross=G, hire_date=date(2020, 1, 1), as_of_year=2026
    )
    assert out == Decimal("1000000.00")


def test_aguinaldo_unknown_hire_date_assumes_full_window():
    out = compute_aguinaldo(monthly_gross=G, hire_date=None, as_of_year=2026)
    assert out == Decimal("1000000.00")


def test_aguinaldo_hired_mid_window_prorates_by_days():
    # Window Dec 1 2025 … Nov 30 2026 = 365 days. Hired Jul 1 2026 →
    # Jul 1..Nov 30 inclusive = 153 days. 1,000,000 × 153/365 = 419,178.08.
    out = compute_aguinaldo(
        monthly_gross=G, hire_date=date(2026, 7, 1), as_of_year=2026
    )
    assert out == (G * Decimal(153) / Decimal(365)).quantize(Decimal("0.01"))
    assert out == Decimal("419178.08")


def test_aguinaldo_hired_after_window_is_zero():
    out = compute_aguinaldo(
        monthly_gross=G, hire_date=date(2027, 1, 1), as_of_year=2026
    )
    assert out == Decimal("0.00")


# ── salario escolar ──────────────────────────────────────────────────────────


def test_salario_escolar_full_year_is_pct_of_annual_gross():
    # 12 × 1,000,000 × 8.33% = 999,600 (≈ one month, by design).
    out = compute_salario_escolar(
        monthly_gross=G, hire_date=date(2020, 1, 1), as_of_year=2026
    )
    assert out == (G * Decimal("12") * SALARIO_ESCOLAR_PCT).quantize(
        Decimal("0.01")
    )
    assert out == Decimal("999600.00")


def test_salario_escolar_prorates_by_calendar_year_days():
    # Calendar 2026 = 365 days. Hired Jul 1 2026 → 184 days (Jul1..Dec31).
    # 12,000,000 × 184/365 × 8.33% = 503,798.79.
    out = compute_salario_escolar(
        monthly_gross=G, hire_date=date(2026, 7, 1), as_of_year=2026
    )
    annual = G * Decimal("12") * Decimal(184) / Decimal(365)
    assert out == (annual * SALARIO_ESCOLAR_PCT).quantize(Decimal("0.01"))


def test_salario_escolar_unknown_hire_full_year():
    out = compute_salario_escolar(
        monthly_gross=G, hire_date=None, as_of_year=2026
    )
    assert out == Decimal("999600.00")
