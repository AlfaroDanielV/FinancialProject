import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class TransferCreate(BaseModel):
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
