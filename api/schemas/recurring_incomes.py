import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


IncomeTypeEnum = Literal[
    "salary", "aguinaldo", "salario_escolar", "freelance", "other"
]
IncomeFrequencyEnum = Literal["weekly", "biweekly", "monthly", "annual"]
IncomeCurrencyEnum = Literal["CRC", "USD"]

# CR cycles are derived from a base salary (Resolución 9.5 + decisions §3.5).
DERIVED_TYPES = frozenset({"aguinaldo", "salario_escolar"})


class RecurringIncomeCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    income_type: IncomeTypeEnum
    # Optional for derived types (aguinaldo / salario_escolar) — server fills
    # from base_salary_link.
    amount: Optional[Decimal] = Field(
        None, gt=0, max_digits=14, decimal_places=2
    )
    # Gross (pre-deduction) figure for a salary captured via the CR calculator.
    # `amount` carries the NET; this keeps the gross for re-edit / recompute.
    gross_monthly: Optional[Decimal] = Field(
        None, gt=0, max_digits=14, decimal_places=2
    )
    currency: IncomeCurrencyEnum = "CRC"
    frequency: IncomeFrequencyEnum
    next_payment_date: date
    base_salary_link_id: Optional[uuid.UUID] = None
    # Employee hire date (fecha de incorporación) — used to prorate aguinaldo
    # and salario escolar. Optional; unknown → full window assumed.
    hire_date: Optional[date] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_cr_cycles_need_link(self):
        if self.income_type in DERIVED_TYPES and self.base_salary_link_id is None:
            raise ValueError(
                f"income_type={self.income_type!r} requiere base_salary_link_id "
                "(aguinaldo y salario_escolar derivan de un salario registrado)"
            )
        if self.income_type not in DERIVED_TYPES and self.amount is None:
            raise ValueError(
                f"amount es requerido para income_type={self.income_type!r}"
            )
        return self


class RecurringIncomeUpdate(BaseModel):
    """Phase 6e B8 — narrow whitelist with extra='forbid'.

    `income_type`, `currency`, and `base_salary_link_id` are immutable.
    Per the router's Resolución 9.8 note, the user re-creates the derived
    row to relink it to a different salary.
    """

    model_config = {"extra": "forbid"}

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    amount: Optional[Decimal] = Field(
        None, gt=0, max_digits=14, decimal_places=2
    )
    gross_monthly: Optional[Decimal] = Field(
        None, gt=0, max_digits=14, decimal_places=2
    )
    frequency: Optional[IncomeFrequencyEnum] = None
    next_payment_date: Optional[date] = None
    hire_date: Optional[date] = None
    is_active: Optional[bool] = None
    archived: Optional[bool] = None
    notes: Optional[str] = None


class RecurringIncomeResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    income_type: str
    amount: Optional[Decimal]
    gross_monthly: Optional[Decimal] = None
    currency: str
    frequency: str
    next_payment_date: date
    base_salary_link_id: Optional[uuid.UUID]
    hire_date: Optional[date] = None
    is_active: bool
    archived: bool = False
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CRCycleDeriveRequest(BaseModel):
    """Optional hire date (fecha de incorporación) for proration. When
    provided it is persisted on the base salary so a re-derive doesn't re-ask;
    when omitted, the salary's stored hire_date (if any) is used, else the
    full window is assumed."""

    model_config = {"extra": "forbid"}

    hire_date: Optional[date] = None


class CRCycleDeriveResponse(BaseModel):
    """Phase 6e B8 — both CR-cycle derived incomes from one salary.

    Idempotent: if either row already exists for the same base salary,
    that row is returned unchanged and only the missing one is created.
    """

    aguinaldo: RecurringIncomeResponse
    salario_escolar: RecurringIncomeResponse
    created_aguinaldo: bool
    created_salario_escolar: bool
