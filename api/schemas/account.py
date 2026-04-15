import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


VALID_ACCOUNT_TYPES = {"checking", "savings", "credit", "investment"}


class AccountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    account_type: str = Field(..., min_length=1, max_length=50)


class AccountUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    is_active: Optional[bool] = None


class AccountResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    account_type: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
