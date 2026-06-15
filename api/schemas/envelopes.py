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
    # Phase 7a: when set, this envelope nests under `parent_id`. The child
    # inherits the parent's class + currency (the values above are ignored for
    # children, validated server-side), and depth = parent.depth + 1 (cap 5).
    parent_id: Optional[uuid.UUID] = None


class EnvelopeUpdate(BaseModel):
    """Narrowed whitelist. `currency` and `period` are immutable post-create
    (changing currency would make a stored limit ambiguous against historical
    spend). `parent_id` is NOT here — re-parenting is out of scope (it would
    force a subtree depth recompute). Editing `envelope_class` is allowed only
    on a root and cascades to the subtree (enforced in the router)."""

    model_config = {"extra": "forbid"}

    name: Optional[str] = Field(None, min_length=1, max_length=120)
    envelope_class: Optional[EnvelopeClass] = None
    limit_amount: Optional[float] = Field(None, gt=0)
    is_active: Optional[bool] = None
    archived: Optional[bool] = None


class EnvelopeResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    parent_id: Optional[uuid.UUID] = None
    depth: int
    name: str
    envelope_class: str
    limit_amount: float
    currency: str
    period: str
    is_active: bool
    archived: bool
    created_at: datetime
    updated_at: datetime
    # Shared envelopes (defaulted; set only when this row is returned to a
    # MEMBER via GET /envelopes). `is_shared=True` means the caller is a member,
    # not the owner — `user_id` is the owner's id. Own rows leave these defaulted.
    is_shared: bool = False
    role: Optional[str] = None  # "member" when shared
    shared_by_name: Optional[str] = None
    member_count: int = 0

    model_config = {"from_attributes": True}


# ── summary (home-tab feed) ───────────────────────────────────────────────────


class EnvelopeSummaryItem(BaseModel):
    id: uuid.UUID
    parent_id: Optional[uuid.UUID] = None
    depth: int = 1
    name: str
    envelope_class: str
    currency: str
    limit_amount: float
    # `spent` is the ROLLED-UP figure (this node + all descendants) — what the
    # bar shows. `direct_spent` is only what's tagged directly to this node.
    spent: float
    direct_spent: float
    # B2 fixed-expense attachment: `reserved` = Σ attached bills/debts unpaid this
    # cycle (rolled up); `available` = limit − reserved − spent (the truly free
    # amount). `remaining` (limit − spent) stays for back-compat.
    reserved: float = 0.0
    available: float = 0.0
    remaining: float  # limit − rolled-up spent
    pct: float  # rolled-up spent / limit, 0..>1 (clamp in UI; >1 = over)
    over_limit: bool
    # Soft allocation (Phase 7a): how the node's budget is split across its
    # direct children. `unallocated` may be negative (over-allocated).
    allocated: float = 0.0
    unallocated: float = 0.0
    over_allocated: bool = False
    # Shared envelopes: when `is_shared`, this item belongs to ANOTHER user's
    # tree the caller is a member of. `spent` is then the SHARED (all-members)
    # rolled-up figure and `your_spent` is the caller's own portion ("Vos: ₡X de
    # ₡Y"). Shared items are display-only — they are NOT counted in by_class /
    # total_* and the byte-locked cashflow/affordability/snapshot consumers
    # filter them out. Defaulted so own-envelope serialization is unchanged.
    is_shared: bool = False
    role: Optional[str] = None  # "member" on shared items; None on own
    shared_by_name: Optional[str] = None
    member_count: int = 0
    your_spent: float = 0.0


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
    # Grand totals across all envelopes in the summary currency (roots only,
    # like total_limit, so subtrees aren't double-counted). They power the
    # native home-screen "Te queda este mes" headline; total_available may be
    # negative when the overall budget is blown.
    total_spent: float = 0.0
    total_reserved: float = 0.0
    total_available: float = 0.0
    monthly_income: Optional[float] = None  # for the "split income" sanity line
