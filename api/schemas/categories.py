import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


CategoryKind = Literal["income", "expense", "both"]


class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    kind: CategoryKind
    color: str = Field(..., pattern=r"^#[0-9A-Fa-f]{6}$")
    icon: Optional[str] = Field(None, max_length=64)


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    kind: Optional[CategoryKind] = None
    color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    icon: Optional[str] = Field(None, max_length=64)
    archived: Optional[bool] = None


class CategoryResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    kind: str
    color: str
    icon: Optional[str]
    is_default: bool
    archived: bool
    transaction_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
