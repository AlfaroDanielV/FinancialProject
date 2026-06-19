"""Derivation logic for CR-specific income cycles (service adapter).

The real formulas live in the pure rules layer ``app/domain/payroll/cr_cycles``;
this adapter feeds them the salary's monthly GROSS (aguinaldo/salario escolar
are gross-based and CCSS-exempt), the employee's hire date (for proration), and
the reference year, and dispatches by income_type. Aguinaldo and salario escolar
are excluded from monthly income, so these amounts only drive the displayed lump
and the Dec/Jan "expected" projection — never the monthly budget.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.payroll import compute_aguinaldo, compute_salario_escolar


def derive_aguinaldo_amount(
    *, monthly_gross: Decimal, hire_date: date | None, as_of_year: int
) -> Decimal:
    """Aguinaldo on gross, prorated by hire date (full window → one month)."""
    return compute_aguinaldo(
        monthly_gross=monthly_gross, hire_date=hire_date, as_of_year=as_of_year
    )


def derive_salario_escolar_amount(
    *, monthly_gross: Decimal, hire_date: date | None, as_of_year: int
) -> Decimal:
    """Salario escolar = 8.33% of gross earned in the calendar year, prorated."""
    return compute_salario_escolar(
        monthly_gross=monthly_gross, hire_date=hire_date, as_of_year=as_of_year
    )


def derive_amount_for(
    income_type: str,
    *,
    monthly_gross: Decimal,
    hire_date: date | None,
    as_of_year: int,
) -> Decimal:
    """Dispatch derivation by income_type. Caller guarantees the type is derivable."""
    if income_type == "aguinaldo":
        return derive_aguinaldo_amount(
            monthly_gross=monthly_gross, hire_date=hire_date, as_of_year=as_of_year
        )
    if income_type == "salario_escolar":
        return derive_salario_escolar_amount(
            monthly_gross=monthly_gross, hire_date=hire_date, as_of_year=as_of_year
        )
    raise ValueError(
        f"income_type={income_type!r} is not a derivable CR cycle"
    )
