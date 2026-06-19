import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class ShortcutTransactionCreate(BaseModel):
    """Payload sent by the iPhone Shortcut."""

    # Always pass a positive number; is_expense controls the sign stored in DB
    amount: float = Field(..., gt=0, description="Amount in CRC (positive)")
    merchant: str = Field(..., min_length=1, max_length=255)
    category: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    is_expense: bool = True
    # Defaults to today when omitted
    transaction_date: Optional[date] = None


class TransactionCreate(BaseModel):
    """General transaction creation payload."""

    # Pass negative for expense, positive for income
    amount: float
    currency: str = "CRC"
    merchant: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    transaction_date: date
    source: str = "manual"
    account_id: Optional[uuid.UUID] = None
    category_id: Optional[uuid.UUID] = None


class TransactionResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    account_id: Optional[uuid.UUID]
    transfer_id: Optional[uuid.UUID] = None
    category_id: Optional[uuid.UUID] = None
    envelope_id: Optional[uuid.UUID] = None
    amount: float
    currency: str
    merchant: Optional[str]
    description: Optional[str]
    category: Optional[str]
    subcategory: Optional[str]
    transaction_date: date
    source: str
    parse_status: str
    status: str
    is_duplicate: bool
    archived: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class TransactionListResponse(BaseModel):
    """Phase 6e B5 — list response with optional cursor.

    `next_cursor` is set when more rows exist past this page; `null` when
    the page exhausts the result set. Callers that paginate with `offset`
    can ignore it; callers that paginate with `cursor` should pass the
    returned value back as `cursor` on the next request.
    """

    total: int
    items: list[TransactionResponse]
    next_cursor: Optional[str] = None


class TransactionUpdate(BaseModel):
    """Phase 6e B4 — editable fields on an existing transaction.

    Transfer, source, status are immutable post-creation. Shadow rows (Phase 6b
    status='shadow') are blocked at the router layer. `account_id` is editable:
    reassigning a movement to an account of a DIFFERENT currency converts the
    amount and rewrites `transactions.currency` to the destination account's
    currency (router-side, via fx.convert) — so per-account balance sums, which
    are computed live, stay correct.
    """

    amount: Optional[float] = None
    merchant: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    category: Optional[str] = Field(None, max_length=100)
    category_id: Optional[uuid.UUID] = None
    # Reassign the movement to another account. Validated active/same-user in
    # the router; cross-currency reassignment converts amount + currency there.
    account_id: Optional[uuid.UUID] = None
    # Envelope budgeting — assign/recategorize the expense to a spending-cap
    # envelope. Validated against the caller's envelopes in the router.
    envelope_id: Optional[uuid.UUID] = None
    transaction_date: Optional[date] = None


class TransactionBulkArchive(BaseModel):
    transaction_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=500)


class TransactionBulkCategorize(BaseModel):
    transaction_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=500)
    category_id: Optional[uuid.UUID] = None


class TransactionBulkResponse(BaseModel):
    updated: int


class TransactionDeleteResponse(BaseModel):
    """Permanent delete result (app 'Eliminar definitivamente')."""

    deleted: bool = True
