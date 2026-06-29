import uuid
from datetime import date, datetime
from decimal import Decimal
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


class ApplePayCapture(BaseModel):
    """Zero-touch contactless capture from the iOS App Intent (Apple Pay /
    Wallet trigger).

    iOS is the extractor (it hands us structured merchant + amount); the backend
    decides deterministically — no LLM on this path. A contactless purchase is
    always an expense, so the sign is applied server-side.

    `amount` is a money STRING parsed to Decimal server-side (never float; see
    `services.money.parse_money_magnitude`). `client_event_id` is the per-tap
    idempotency key → stored as `source_ref='apple_pay:{id}'` so an offline
    retry of the same tap is a no-op.
    """

    amount: str = Field(..., min_length=1, max_length=64)
    currency: str = Field(default="CRC", max_length=10)
    merchant: str = Field(..., min_length=1, max_length=255)
    client_event_id: str = Field(..., min_length=1, max_length=200)
    card_hint: Optional[str] = Field(default=None, max_length=255)
    # Defaults to the user's CR-today when omitted.
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
    goal_id: Optional[uuid.UUID] = None
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


class RegisterAsPaymentRequest(BaseModel):
    """Turn a positive movement on a credit account into a card payment: a
    transfer from a fund account → the card, then the original row is removed.

    `amount` defaults to the row's own amount and is expressed in the card (row)
    currency. `fx_rate` is required only when the source account is a different
    currency (units of source currency per 1 unit of card currency, per the
    TransferCreate convention).
    """

    source_account_id: uuid.UUID
    amount: Optional[Decimal] = Field(None, gt=0)
    occurred_at: Optional[datetime] = None
    fx_rate: Optional[Decimal] = Field(None, gt=0)


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
