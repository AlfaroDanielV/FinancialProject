import uuid
from datetime import date, datetime, time
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from ..models.enums import CustomEventType


class CustomEventCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    event_type: CustomEventType
    event_date: date
    is_all_day: bool = True
    event_time: Optional[time] = None
    amount: Optional[float] = Field(None, gt=0)
    currency: str = Field("CRC", min_length=3, max_length=3)
    recurrence_rule: Optional[str] = None

    @model_validator(mode="after")
    def _validate(self):
        if not self.is_all_day and self.event_time is None:
            raise ValueError("event_time es requerido cuando is_all_day=false")
        return self


class CustomEventUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    event_type: Optional[CustomEventType] = None
    event_date: Optional[date] = None
    is_all_day: Optional[bool] = None
    event_time: Optional[time] = None
    amount: Optional[float] = Field(None, gt=0)
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    recurrence_rule: Optional[str] = None
    is_active: Optional[bool] = None


class CustomEventResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: Optional[str]
    event_type: str
    event_date: date
    is_all_day: bool
    event_time: Optional[time]
    amount: Optional[float]
    currency: str
    recurrence_rule: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
