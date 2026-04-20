import uuid
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


GoalStatusEnum = Literal["active", "paused", "completed", "abandoned"]


class GoalCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    target_amount: float = Field(..., gt=0)
    deadline: Optional[date] = None
    priority: int = Field(3, ge=1, le=5)
    monthly_contribution: Optional[float] = Field(None, gt=0)


class GoalUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    target_amount: Optional[float] = Field(None, gt=0)
    current_amount: Optional[float] = Field(None, ge=0)
    deadline: Optional[date] = None
    priority: Optional[int] = Field(None, ge=1, le=5)
    monthly_contribution: Optional[float] = Field(None, gt=0)
    status: Optional[GoalStatusEnum] = None


class GoalResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    target_amount: float
    current_amount: float
    deadline: Optional[date]
    monthly_contribution: Optional[float]
    priority: int
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class GoalProgress(BaseModel):
    id: uuid.UUID
    name: str
    target_amount: float
    current_amount: float
    remaining: float
    progress_percent: float
    months_remaining: Optional[int]
    monthly_needed: Optional[float]
    on_track: Optional[bool]
    status: str


class ContributeRequest(BaseModel):
    amount: float = Field(..., gt=0)
