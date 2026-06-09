"""Deterministic Costa Rican payroll calculations (rules layer)."""
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
    "UnconfiguredYearError",
    "configured_years",
    "get_year_rates",
]
