import uuid
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class BudgetCreate(BaseModel):
    category: str = Field(..., min_length=1, max_length=100)
    amount_limit: float = Field(..., gt=0)
    period: Literal["weekly", "monthly"]
    start_date: Optional[date] = None


class BudgetUpdate(BaseModel):
    category: Optional[str] = Field(None, min_length=1, max_length=100)
    amount_limit: Optional[float] = Field(None, gt=0)
    period: Optional[Literal["weekly", "monthly"]] = None
    start_date: Optional[date] = None
    is_active: Optional[bool] = None


class BudgetResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    category: str
    amount_limit: float
    period: str
    start_date: Optional[date]
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class BudgetStatus(BaseModel):
    id: uuid.UUID
    category: str
    amount_limit: float
    spent: float
    remaining: float
    percent_used: float
    period: str
    is_over_budget: bool
