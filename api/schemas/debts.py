import uuid
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

VALID_DEBT_TYPES = {"mortgage", "credit_card", "auto_loan", "personal_loan", "student_loan", "other"}
DebtTypeEnum = Literal["mortgage", "credit_card", "auto_loan", "personal_loan", "student_loan", "other"]
RateTypeEnum = Literal["fixed", "variable"]
StrategyEnum = Literal["increase_payment", "lump_sum", "aguinaldo", "reduce_term", "reduce_payment"]


class DebtCreate(BaseModel):
    name: str = Field(..., min_length=1)
    debt_type: DebtTypeEnum
    original_amount: float = Field(..., gt=0)
    current_balance: float = Field(..., gt=0)
    interest_rate: float = Field(..., ge=0, le=1)
    minimum_payment: float = Field(..., gt=0)
    payment_due_day: int = Field(..., ge=1, le=31)
    account_id: Optional[uuid.UUID] = None
    lender: Optional[str] = None
    term_months: Optional[int] = Field(None, gt=0)
    start_date: Optional[date] = None
    maturity_date: Optional[date] = None
    currency: str = "CRC"
    notes: Optional[str] = None
    rate_type: RateTypeEnum = "fixed"
    rate_reference: Optional[str] = None
    rate_spread: Optional[float] = Field(None, ge=0, le=1)
    prepayment_penalty_pct: float = Field(0, ge=0, le=0.05)
    payments_made: int = Field(0, ge=0)
    includes_insurance: bool = False
    insurance_monthly: Optional[float] = Field(None, ge=0)

    @model_validator(mode="after")
    def validate_variable_rate(self):
        if self.rate_type == "variable" and not self.rate_reference:
            raise ValueError("rate_reference is required for variable rate debts")
        return self


class DebtUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    debt_type: Optional[DebtTypeEnum] = None
    original_amount: Optional[float] = Field(None, gt=0)
    current_balance: Optional[float] = Field(None, gt=0)
    interest_rate: Optional[float] = Field(None, ge=0, le=1)
    minimum_payment: Optional[float] = Field(None, gt=0)
    payment_due_day: Optional[int] = Field(None, ge=1, le=31)
    account_id: Optional[uuid.UUID] = None
    lender: Optional[str] = None
    term_months: Optional[int] = Field(None, gt=0)
    start_date: Optional[date] = None
    maturity_date: Optional[date] = None
    currency: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None
    rate_type: Optional[RateTypeEnum] = None
    rate_reference: Optional[str] = None
    rate_spread: Optional[float] = Field(None, ge=0, le=1)
    prepayment_penalty_pct: Optional[float] = Field(None, ge=0, le=0.05)
    payments_made: Optional[int] = Field(None, ge=0)
    includes_insurance: Optional[bool] = None
    insurance_monthly: Optional[float] = Field(None, ge=0)


class DebtResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    account_id: Optional[uuid.UUID]
    name: str
    debt_type: str
    lender: Optional[str]
    original_amount: float
    current_balance: float
    interest_rate: float
    minimum_payment: float
    payment_due_day: int
    term_months: Optional[int]
    start_date: Optional[date]
    maturity_date: Optional[date]
    currency: str
    notes: Optional[str]
    rate_type: str
    rate_reference: Optional[str]
    rate_spread: Optional[float]
    prepayment_penalty_pct: float
    payments_made: int
    includes_insurance: bool
    insurance_monthly: Optional[float]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DebtSummary(BaseModel):
    id: uuid.UUID
    name: str
    debt_type: str
    current_balance: float
    interest_rate: float
    minimum_payment: float
    payment_due_day: int
    is_active: bool

    model_config = {"from_attributes": True}


class DebtPaymentCreate(BaseModel):
    payment_date: date
    amount_paid: float = Field(..., gt=0)
    principal_portion: Optional[float] = None
    interest_portion: Optional[float] = None
    extra_payment: Optional[float] = None
    transaction_id: Optional[uuid.UUID] = None
    remaining_balance: Optional[float] = None
    notes: Optional[str] = None


class DebtPaymentResponse(BaseModel):
    id: uuid.UUID
    debt_id: uuid.UUID
    transaction_id: Optional[uuid.UUID]
    payment_date: date
    amount_paid: float
    principal_portion: Optional[float]
    interest_portion: Optional[float]
    extra_payment: Optional[float]
    remaining_balance: float
    notes: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class UpcomingPayment(BaseModel):
    debt_id: uuid.UUID
    name: str
    debt_type: str
    minimum_payment: float
    payment_due_day: int
    current_balance: float


class DebtOverview(BaseModel):
    total_debt: float
    total_minimum_monthly: float
    debts_by_type: dict[str, float]
    upcoming_payments: list[UpcomingPayment]


# ── Amortization schemas ──────────────────────────────────────────────────────

class AmortizationRow(BaseModel):
    month_number: int
    payment_date: date
    payment_amount: float
    principal_portion: float
    interest_portion: float
    insurance_portion: float = 0.0
    extra_payment: float = 0.0
    remaining_balance: float
    cumulative_interest: float
    cumulative_principal: float


class AmortizationSchedule(BaseModel):
    debt_id: uuid.UUID
    debt_name: str
    current_balance: float
    interest_rate: float
    monthly_payment: float
    total_months: int
    total_interest: float
    total_principal: float
    total_payments: float
    payoff_date: Optional[date]
    variable_rate_notice: Optional[str] = None
    schedule: list[AmortizationRow]


# ── Early payoff schemas ──────────────────────────────────────────────────────

class EarlyPayoffRequest(BaseModel):
    strategy: StrategyEnum
    extra_monthly: Optional[float] = Field(None, gt=0)
    lump_sum_amount: Optional[float] = Field(None, gt=0)
    lump_sum_month: Optional[int] = Field(None, ge=1)
    aguinaldo_amount: Optional[float] = Field(None, gt=0)
    target_months: Optional[int] = Field(None, gt=0)
    target_payment: Optional[float] = Field(None, gt=0)
    projected_rate: Optional[float] = Field(None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_strategy_params(self):
        s = self.strategy
        if s == "increase_payment" and not self.extra_monthly:
            raise ValueError("extra_monthly is required for increase_payment strategy")
        if s == "lump_sum" and not self.lump_sum_amount:
            raise ValueError("lump_sum_amount is required for lump_sum strategy")
        if s == "aguinaldo" and not self.aguinaldo_amount:
            raise ValueError("aguinaldo_amount is required for aguinaldo strategy")
        if s == "reduce_term" and not self.target_months:
            raise ValueError("target_months is required for reduce_term strategy")
        if s == "reduce_payment" and not self.target_payment:
            raise ValueError("target_payment is required for reduce_payment strategy")
        return self


class ScheduleSummary(BaseModel):
    monthly_payment: float
    total_months: int
    total_interest: float
    total_paid: float
    payoff_date: Optional[date]


class SavingsSummary(BaseModel):
    months_saved: int
    interest_saved: float
    total_saved: float
    new_payoff_date: Optional[date]
    prepayment_penalty_applies: bool
    prepayment_penalty_amount: float


class EarlyPayoffResponse(BaseModel):
    original_schedule: ScheduleSummary
    proposed_schedule: ScheduleSummary
    savings: SavingsSummary
    monthly_impact: float
    strategy: str
    currency: str
    variable_rate_notice: Optional[str] = None


# ── Payoff strategies schemas ─────────────────────────────────────────────────

class DebtPayoffEntry(BaseModel):
    debt_id: str
    name: str
    debt_type: str
    current_balance: float
    interest_rate: float
    minimum_payment: float
    payoff_month: int
    total_interest_paid: float


class PayoffStrategyResult(BaseModel):
    strategy_name: str
    order: list[DebtPayoffEntry]
    total_months: int
    total_interest: float
    months_saved_vs_minimum: int
    interest_saved_vs_minimum: float


class PayoffStrategiesResponse(BaseModel):
    extra_monthly: float
    minimum_only: PayoffStrategyResult
    snowball: PayoffStrategyResult
    avalanche: PayoffStrategyResult
    recommendation: str
