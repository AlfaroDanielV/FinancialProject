"""Pydantic schemas for envelope budgeting ("Sobres").

Spending-cap envelopes: user-named buckets tied to a class
(needs/wants/savings/investing) with a monthly limit. Spend is computed live
from transactions; the summary is the home-tab feed. Amounts are floats in the
API (matching debts/transactions), stored as NUMERIC.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

EnvelopeClass = Literal["needs", "wants", "savings", "investing"]
CurrencyLit = Literal["CRC", "USD"]


class EnvelopeCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    envelope_class: EnvelopeClass
    limit_amount: float = Field(..., gt=0)
    currency: CurrencyLit = "CRC"


class EnvelopeUpdate(BaseModel):
    """Narrowed whitelist. `currency` and `period` are immutable post-create
    (changing currency would make a stored limit ambiguous against historical
    spend)."""

    model_config = {"extra": "forbid"}

    name: Optional[str] = Field(None, min_length=1, max_length=120)
    envelope_class: Optional[EnvelopeClass] = None
    limit_amount: Optional[float] = Field(None, gt=0)
    is_active: Optional[bool] = None
    archived: Optional[bool] = None


class EnvelopeResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    envelope_class: str
    limit_amount: float
    currency: str
    period: str
    is_active: bool
    archived: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── summary (home-tab feed) ───────────────────────────────────────────────────


class EnvelopeSummaryItem(BaseModel):
    id: uuid.UUID
    name: str
    envelope_class: str
    currency: str
    limit_amount: float
    spent: float
    remaining: float
    pct: float  # spent / limit, 0..>1 (clamp in UI; >1 = over)
    over_limit: bool


class EnvelopeClassSubtotal(BaseModel):
    envelope_class: str
    limit_total: float
    spent_total: float
    over_limit: bool


class EnvelopeSummaryResponse(BaseModel):
    period: str  # e.g. "2026-06"
    currency: str
    envelopes: list[EnvelopeSummaryItem] = Field(default_factory=list)
    by_class: list[EnvelopeClassSubtotal] = Field(default_factory=list)
    total_limit: float
    monthly_income: Optional[float] = None  # for the "split income" sanity line
