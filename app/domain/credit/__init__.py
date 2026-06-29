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
from .statement_cycle import (
    is_statement_settled,
    last_corte,
    statement_due_date,
)

__all__ = [
    "MonthRow",
    "PayoffProjection",
    "compute_minimum",
    "payment_for_months",
    "project_fixed_payment",
    "project_minimum_only",
    "is_statement_settled",
    "last_corte",
    "statement_due_date",
]
