import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from ..models.enums import BillCategory, BillFrequency, BillOccurrenceStatus


class RecurringBillCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    provider: Optional[str] = Field(None, max_length=255)
    category: BillCategory
    amount_expected: Optional[float] = Field(None, gt=0)
    currency: str = Field("CRC", min_length=3, max_length=3)
    is_variable_amount: bool = False
    account_id: Optional[uuid.UUID] = None
    frequency: BillFrequency
    day_of_month: Optional[int] = Field(None, ge=1, le=31)
    recurrence_rule: Optional[str] = None
    start_date: date
    end_date: Optional[date] = None
    lead_time_days: int = Field(0, ge=0, le=90)
    notes: Optional[str] = None
    linked_loan_id: Optional[uuid.UUID] = None

    @model_validator(mode="after")
    def _validate(self):
        if self.frequency == BillFrequency.CUSTOM and not self.recurrence_rule:
            raise ValueError(
                "recurrence_rule es requerido cuando frequency='custom'"
            )
        if (
            not self.is_variable_amount
            and self.amount_expected is None
        ):
            raise ValueError(
                "amount_expected es requerido cuando is_variable_amount=false"
            )
        if self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date no puede ser anterior a start_date")
        return self


class RecurringBillUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    provider: Optional[str] = Field(None, max_length=255)
    category: Optional[BillCategory] = None
    amount_expected: Optional[float] = Field(None, gt=0)
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    is_variable_amount: Optional[bool] = None
    account_id: Optional[uuid.UUID] = None
    frequency: Optional[BillFrequency] = None
    day_of_month: Optional[int] = Field(None, ge=1, le=31)
    recurrence_rule: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    lead_time_days: Optional[int] = Field(None, ge=0, le=90)
    is_active: Optional[bool] = None
    notes: Optional[str] = None
    linked_loan_id: Optional[uuid.UUID] = None


class RecurringBillResponse(BaseModel):
    id: uuid.UUID
    name: str
    provider: Optional[str]
    category: str
    amount_expected: Optional[float]
    currency: str
    is_variable_amount: bool
    account_id: Optional[uuid.UUID]
    frequency: str
    day_of_month: Optional[int]
    recurrence_rule: Optional[str]
    start_date: date
    end_date: Optional[date]
    lead_time_days: int
    is_active: bool
    notes: Optional[str]
    linked_loan_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── bill_occurrences ──────────────────────────────────────────────────────────


class BillOccurrenceResponse(BaseModel):
    id: uuid.UUID
    recurring_bill_id: uuid.UUID
    due_date: date
    amount_expected: Optional[float]
    amount_paid: Optional[float]
    status: str
    paid_at: Optional[datetime]
    transaction_id: Optional[uuid.UUID]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MarkPaidRequest(BaseModel):
    transaction_id: Optional[uuid.UUID] = None
    amount_paid: Optional[float] = Field(None, gt=0)
    paid_at: Optional[datetime] = None
    notes: Optional[str] = None


class MarkPaidResponse(BaseModel):
    occurrence: BillOccurrenceResponse
    amount_delta_pct: Optional[float] = None
    warning: Optional[str] = None


class SkipRequest(BaseModel):
    notes: Optional[str] = None


# ── status enum re-export for routers ─────────────────────────────────────────

__all__ = [
    "RecurringBillCreate",
    "RecurringBillUpdate",
    "RecurringBillResponse",
    "BillOccurrenceResponse",
    "MarkPaidRequest",
    "MarkPaidResponse",
    "SkipRequest",
    "BillOccurrenceStatus",
]
