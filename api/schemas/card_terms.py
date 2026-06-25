"""Phase 7b — credit-card terms schemas (B3) + analysis response (B4).

Rates are 0–1 fractions everywhere (18% → 0.18), matching `debts.interest_rate`.
`CardTermsExtraction` mirrors `DebtTermsExtraction`: every field Optional except
`confidence` so the model can honestly leave unknowns null — the form asks for
whatever is missing. "LLM extracts; rules decide": the deterministic
PUT /accounts/{id}/card-terms is the only write path.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field


class CardTermsCreate(BaseModel):
    model_config = {"extra": "forbid"}

    annual_interest_rate: Decimal = Field(..., ge=0, le=1)
    cash_advance_rate: Optional[Decimal] = Field(None, ge=0, le=1)
    minimum_payment_pct: Decimal = Field(..., gt=0, le=1)
    minimum_payment_floor: Optional[Decimal] = Field(None, gt=0)
    credit_limit: Optional[Decimal] = Field(None, gt=0)
    statement_day: Optional[int] = Field(None, ge=1, le=31)
    payment_due_day: int = Field(..., ge=1, le=31)
    # Recurring-payment preference: 'minimum' (default) or 'full' (pago de
    # contado). Selects which live figure the upcoming-payment projection
    # surfaces; budget/affordability/reservation stay on the minimum.
    payment_mode: Literal["minimum", "full"] = "minimum"
    # B5 obligation attachment: the card's minimum reserves inside this
    # envelope while unpaid (validated active + same-user in the router).
    envelope_id: Optional[uuid.UUID] = None
    notes: Optional[str] = Field(None, max_length=500)


class CardTermsUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    annual_interest_rate: Optional[Decimal] = Field(None, ge=0, le=1)
    cash_advance_rate: Optional[Decimal] = Field(None, ge=0, le=1)
    minimum_payment_pct: Optional[Decimal] = Field(None, gt=0, le=1)
    minimum_payment_floor: Optional[Decimal] = Field(None, gt=0)
    credit_limit: Optional[Decimal] = Field(None, gt=0)
    statement_day: Optional[int] = Field(None, ge=1, le=31)
    payment_due_day: Optional[int] = Field(None, ge=1, le=31)
    payment_mode: Optional[Literal["minimum", "full"]] = None
    notes: Optional[str] = Field(None, max_length=500)


class CardTermsResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    account_id: uuid.UUID
    annual_interest_rate: Decimal
    cash_advance_rate: Optional[Decimal]
    minimum_payment_pct: Decimal
    minimum_payment_floor: Optional[Decimal]
    credit_limit: Optional[Decimal]
    statement_day: Optional[int]
    payment_due_day: int
    payment_mode: str
    envelope_id: Optional[uuid.UUID]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CardTermsExtraction(BaseModel):
    """Phase 7b B3 — card terms parsed from an uploaded contract (or
    statement) PDF.

    Returned by `POST /accounts/parse-card-document` to PRE-FILL the native
    card form; it does NOT create anything. Rates are 0–1 fractions.

    Dual-currency CR cards: the base fields are the COLONES terms; the
    `*_usd` fields are filled only when the document lists separate dollar
    terms — their presence is the signal that the card operates in both
    currencies (the form then creates one credit account per currency).
    """

    issuer: Optional[str] = None
    credit_limit: Optional[float] = Field(None, gt=0)
    statement_balance: Optional[float] = Field(None, ge=0)
    annual_interest_rate: Optional[float] = Field(None, ge=0, le=1)
    cash_advance_rate: Optional[float] = Field(None, ge=0, le=1)
    annual_interest_rate_usd: Optional[float] = Field(None, ge=0, le=1)
    credit_limit_usd: Optional[float] = Field(None, gt=0)
    statement_balance_usd: Optional[float] = Field(None, ge=0)
    minimum_payment_pct: Optional[float] = Field(None, gt=0, le=1)
    minimum_payment_amount: Optional[float] = Field(None, gt=0)
    statement_day: Optional[int] = Field(None, ge=1, le=31)
    payment_due_day: Optional[int] = Field(None, ge=1, le=31)
    currency: Optional[str] = None
    confidence: float = Field(..., ge=0, le=1)


# ── B4: deterministic card analysis (engine output over the live balance) ─────


class CardPaymentStrategy(BaseModel):
    label: str
    monthly_payment: Decimal
    months: Optional[int]
    total_interest: Decimal
    interest_saved_vs_minimum: Optional[Decimal]


class CardAnalysisResponse(BaseModel):
    account_id: uuid.UUID
    currency: str
    as_of: date
    balance_owed: Decimal
    credit_limit: Optional[Decimal]
    available_credit: Optional[Decimal]
    minimum_payment: Decimal
    monthly_interest_cost: Decimal
    months_to_payoff_minimum: Optional[int]
    total_interest_minimum: Optional[Decimal]
    total_paid_minimum: Optional[Decimal]
    never_pays_off: bool
    strategies: list[CardPaymentStrategy]
