"""Deterministic Costa Rican payroll calculations (rules layer)."""
from .cr_cycles import (
    SALARIO_ESCOLAR_PCT,
    compute_aguinaldo,
    compute_salario_escolar,
)
from .cr_salary import (
    CcssBreakdown,
    IsrBaseMode,
    IsrBreakdown,
    IsrTramoDetail,
    SalaryBreakdown,
    compute_net_salary,
)
from .rates import UnconfiguredYearError, configured_years, get_year_rates

__all__ = [
    "CcssBreakdown",
    "IsrBaseMode",
    "IsrBreakdown",
    "IsrTramoDetail",
    "SalaryBreakdown",
    "compute_net_salary",
    "SALARIO_ESCOLAR_PCT",
    "compute_aguinaldo",
    "compute_salario_escolar",
    "UnconfiguredYearError",
    "configured_years",
    "get_year_rates",
]
