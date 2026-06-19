import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class TransferCreate(BaseModel):
    """Create a transfer between two of the caller's accounts.

    `currency` is the currency the typed `amount` is expressed in (the input
    currency / mode selector): it must equal the from- OR the to-account
    currency. `fx_rate` (required only when the two accounts differ in currency)
    has a single canonical direction — **units of the from-account (funding)
    currency per 1 unit of the to-account (credit/destination) currency**,
    e.g. CRC per 1 USD ≈ 520. The service derives the debited (funding) and
    applied (destination) amounts from these.
    """

    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount: Decimal = Field(..., gt=0)
    currency: str = Field("CRC", min_length=3, max_length=3)
    fx_rate: Optional[Decimal] = Field(None, gt=0)
    occurred_at: Optional[datetime] = None
    notes: Optional[str] = Field(None, max_length=500)


class TransferResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount: Decimal
    currency: str
    fx_rate: Optional[Decimal]
    occurred_at: datetime
    notes: Optional[str]
    created_at: datetime
    debit_transaction_id: Optional[uuid.UUID] = None
    credit_transaction_id: Optional[uuid.UUID] = None

    model_config = {"from_attributes": True}
