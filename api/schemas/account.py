import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field


VALID_ACCOUNT_TYPES = {"checking", "savings", "credit", "investment"}
CurrencyEnum = Literal["CRC", "USD"]


class AccountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    account_type: str = Field(..., min_length=1, max_length=50)
    currency: CurrencyEnum = "CRC"
    initial_balance: Decimal = Field(default=Decimal("0"), max_digits=14, decimal_places=2)


class AccountUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    account_type: Optional[str] = Field(None, min_length=1, max_length=50)
    is_active: Optional[bool] = None
    archived: Optional[bool] = None


class AccountResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    account_type: str
    currency: str
    initial_balance: Decimal
    is_active: bool
    archived: bool
    created_at: datetime
    current_balance: Optional[Decimal] = None
    month_start_balance: Optional[Decimal] = None
    # True when the real balance is unconfirmed (no anchor, or only the 0035
    # 'migrated' backfill) → the app shows a "confirmá tu saldo real" nudge.
    needs_balance_confirmation: bool = False

    model_config = {"from_attributes": True}


class AnchorRequest(BaseModel):
    """Set or correct an account's real balance (bank reconciliation)."""

    value: Decimal = Field(..., max_digits=14, decimal_places=2)
    note: Optional[str] = Field(None, max_length=500)


class AnchorResponse(BaseModel):
    account_id: uuid.UUID
    value: Decimal  # the newly anchored real balance
    effective_date: date
    delta: Decimal  # value − previously-projected balance (the reconciled gap)
    ajuste_transaction_id: Optional[uuid.UUID] = None
    current_balance: Decimal  # == value (post-anchor; no future-dated txns)


class AccountDeleteImpactResponse(BaseModel):
    """Phase 7b B2 — preview of what a hard delete removes / detaches. The
    native confirm sheet shows these counts before asking for the typed name."""

    transactions: int
    transfers: int
    debts_detached: int
    bills_detached: int
    goals_detached: int


class AccountHardDeleteResponse(BaseModel):
    """Phase 7b B2 — snapshot of the deleted account + what was removed."""

    account: AccountResponse
    impact: AccountDeleteImpactResponse
