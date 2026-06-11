"""Pure revolving-credit domain layer (Phase 7b B4).

Same contract as `app/domain/payroll`: no LLM, no DB, no network — only
deterministic math. The LLM narrates these results; it never calculates
interest ("LLM extracts; rules decide").
"""
from .revolving import (
    MonthRow,
    PayoffProjection,
    compute_minimum,
    payment_for_months,
    project_fixed_payment,
    project_minimum_only,
)

__all__ = [
    "MonthRow",
    "PayoffProjection",
    "compute_minimum",
    "payment_for_months",
    "project_fixed_payment",
    "project_minimum_only",
]
