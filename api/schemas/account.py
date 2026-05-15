import uuid
from datetime import datetime
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

    model_config = {"from_attributes": True}
