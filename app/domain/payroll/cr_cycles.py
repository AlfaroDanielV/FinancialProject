"""Deterministic Costa Rican income-cycle calculators: aguinaldo + salario
escolar. Rules layer — NO LLM, NO network, NO DB. The service layer feeds it a
monthly gross + hire date and stores the result; the LLM never recomputes.

**Aguinaldo** (Ley 2412 / Código de Trabajo): the sum of ordinary GROSS salary
earned between **Dec 1 (prev year)** and **Nov 30 (current year)**, divided by
12. With a full window worked at a constant salary that equals exactly one
month's gross. Hired part-way through the window → proportional to days worked.
Aguinaldo is exempt from CCSS and (up to 1/12 of annual income) from ISR, so it
is modeled on gross.

**Salario escolar** (public sector Decreto 22965-MTSS; voluntary private): the
sum of GROSS salary earned in the **calendar year (Jan 1 – Dec 31)** times a
percentage (default 8.33% ≈ 1/12 — the public-sector / maximum private rate).
Also proportional to days worked. Modeled on the gross accrual lump; per-sector
deductions (public-sector CCSS on the payout) are out of scope here.
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

_CENTS = Decimal("0.01")

# Default salario-escolar rate: 8.33% (public sector / max voluntary private).
SALARIO_ESCOLAR_PCT = Decimal("0.0833")


def _q(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _days_inclusive(start: date, end: date) -> int:
    """Inclusive day count; 0 when the range is empty (start after end)."""
    if start > end:
        return 0
    return (end - start).days + 1


def _worked_fraction(
    *, hire_date: date | None, window_start: date, window_end: date
) -> Decimal:
    """Fraction of the accrual window actually worked, by day count.

    Unknown hire date (or hired on/before the window start) → 1 (full window).
    Hired after the window end → 0.
    """
    if hire_date is None or hire_date <= window_start:
        return Decimal("1")
    worked = _days_inclusive(hire_date, window_end)
    total = _days_inclusive(window_start, window_end)
    if total == 0:  # pragma: no cover - windows are always non-empty
        return Decimal("0")
    return Decimal(worked) / Decimal(total)


def compute_aguinaldo(
    *, monthly_gross: Decimal, hire_date: date | None, as_of_year: int
) -> Decimal:
    """Aguinaldo paid in December `as_of_year`.

    Window: Dec 1 (as_of_year - 1) … Nov 30 (as_of_year). Full window worked →
    one month's gross; otherwise prorated by days worked.
    """
    window_start = date(as_of_year - 1, 12, 1)
    window_end = date(as_of_year, 11, 30)
    fraction = _worked_fraction(
        hire_date=hire_date, window_start=window_start, window_end=window_end
    )
    return _q(Decimal(monthly_gross) * fraction)


def compute_salario_escolar(
    *,
    monthly_gross: Decimal,
    hire_date: date | None,
    as_of_year: int,
    pct: Decimal = SALARIO_ESCOLAR_PCT,
) -> Decimal:
    """Salario escolar accrued over calendar year `as_of_year` (paid Jan next).

    `pct` × annual gross earned. Full year worked → ≈ one month's gross
    (12 × 8.33%); otherwise prorated by days worked in the calendar year.
    """
    window_start = date(as_of_year, 1, 1)
    window_end = date(as_of_year, 12, 31)
    fraction = _worked_fraction(
        hire_date=hire_date, window_start=window_start, window_end=window_end
    )
    annual_gross_earned = Decimal(monthly_gross) * Decimal("12") * fraction
    return _q(annual_gross_earned * Decimal(pct))
