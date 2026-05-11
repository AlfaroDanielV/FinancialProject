"""Derivation logic for CR-specific income cycles.

Aguinaldo (Ley 2412): 1/12 of annual salary. With 12 full months of work,
this equals one monthly salary; paid in December.

Salario escolar (Decreto Ejecutivo 22965-MTSS for public sector;
voluntary in private): 8.33% (1/12) accumulated monthly, paid in January.
With 12 full months of work, this also equals one monthly salary.

Both are modeled identically in v1 (= one monthly salary). If real-world
edge cases surface (e.g. partial year, bonuses), refine here — the
service is the only place the formulas live.
"""
from __future__ import annotations

from decimal import Decimal


def derive_aguinaldo_amount(monthly_salary: Decimal) -> Decimal:
    """Aguinaldo = monthly salary (full-year work assumption)."""
    return monthly_salary


def derive_salario_escolar_amount(monthly_salary: Decimal) -> Decimal:
    """Salario escolar = monthly salary (full-year work assumption)."""
    return monthly_salary


def derive_amount_for(income_type: str, monthly_salary: Decimal) -> Decimal:
    """Dispatch derivation by income_type. Caller guarantees the type is derivable."""
    if income_type == "aguinaldo":
        return derive_aguinaldo_amount(monthly_salary)
    if income_type == "salario_escolar":
        return derive_salario_escolar_amount(monthly_salary)
    raise ValueError(
        f"income_type={income_type!r} is not a derivable CR cycle"
    )
