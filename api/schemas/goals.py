import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


GoalStatusEnum = Literal[
    "active", "achieved", "abandoned", "paused", "completed"
]
GoalStatusStored = Literal["active", "achieved", "abandoned", "paused"]


class GoalCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    target_amount: Decimal = Field(..., gt=0)
    target_currency: Literal["CRC", "USD"] = "CRC"
    target_date: Optional[date] = None
    # Legacy input alias from pre-6e API.
    deadline: Optional[date] = None
    current_amount: Decimal = Field(Decimal("0"), ge=0)
    priority: int = Field(3, ge=1, le=5)
    monthly_contribution: Optional[Decimal] = Field(None, gt=0)
    linked_account_id: Optional[uuid.UUID] = None

    @model_validator(mode="after")
    def fill_target_date_from_legacy_deadline(self) -> "GoalCreate":
        if self.target_date is None and self.deadline is not None:
            self.target_date = self.deadline
        return self


class GoalUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    target_amount: Optional[Decimal] = Field(None, gt=0)
    target_currency: Optional[Literal["CRC", "USD"]] = None
    current_amount: Optional[Decimal] = Field(None, ge=0)
    target_date: Optional[date] = None
    # Legacy input alias from pre-6e API.
    deadline: Optional[date] = None
    priority: Optional[int] = Field(None, ge=1, le=5)
    monthly_contribution: Optional[Decimal] = Field(None, gt=0)
    status: Optional[GoalStatusEnum] = None
    linked_account_id: Optional[uuid.UUID] = None


class GoalResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    target_amount: Decimal
    target_currency: str
    current_amount: Decimal
    target_date: Optional[date]
    # Kept in responses so older clients do not break during the 6e rollout.
    deadline: Optional[date] = None
    monthly_contribution: Optional[Decimal]
    priority: int
    status: str
    linked_account_id: Optional[uuid.UUID]
    created_at: datetime

    model_config = {"from_attributes": True}


class GoalContributionCreate(BaseModel):
    amount: Decimal = Field(..., gt=0)
    occurred_at: Optional[datetime] = None
    transaction_id: Optional[uuid.UUID] = None


class GoalContributionResponse(BaseModel):
    id: uuid.UUID
    goal_id: uuid.UUID
    transaction_id: Optional[uuid.UUID]
    amount: Decimal
    occurred_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class GoalContributionResult(BaseModel):
    goal: GoalResponse
    contribution: GoalContributionResponse


class GoalProgress(BaseModel):
    id: uuid.UUID
    name: str
    target_amount: Decimal
    target_currency: str
    current_amount: Decimal
    remaining: Decimal
    progress_percent: float
    months_remaining: Optional[int]
    monthly_needed: Optional[Decimal]
    on_track: Optional[bool]
    status: str


class ContributeRequest(GoalContributionCreate):
    """Legacy request body for POST /goals/{id}/contribute."""
