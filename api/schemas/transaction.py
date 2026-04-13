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


class TransactionResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    account_id: Optional[uuid.UUID]
    amount: float
    currency: str
    merchant: Optional[str]
    description: Optional[str]
    category: Optional[str]
    subcategory: Optional[str]
    transaction_date: date
    source: str
    parse_status: str
    is_duplicate: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class TransactionListResponse(BaseModel):
    total: int
    items: list[TransactionResponse]
