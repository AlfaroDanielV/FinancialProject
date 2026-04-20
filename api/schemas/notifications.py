import uuid
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from ..models.enums import (
    BillCategory,
    NotificationChannel,
    NotificationScope,
)


# ── notification_rules ────────────────────────────────────────────────────────


class NotificationRuleCreate(BaseModel):
    scope: NotificationScope
    recurring_bill_id: Optional[uuid.UUID] = None
    custom_event_id: Optional[uuid.UUID] = None
    category: Optional[BillCategory] = None
    advance_days: list[int] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _validate(self):
        scope = self.scope
        if scope == NotificationScope.BILL:
            if self.recurring_bill_id is None:
                raise ValueError("recurring_bill_id es requerido para scope=bill")
            if self.custom_event_id is not None or self.category is not None:
                raise ValueError(
                    "scope=bill no admite custom_event_id ni category"
                )
        elif scope == NotificationScope.EVENT:
            if self.custom_event_id is None:
                raise ValueError("custom_event_id es requerido para scope=event")
            if self.recurring_bill_id is not None or self.category is not None:
                raise ValueError(
                    "scope=event no admite recurring_bill_id ni category"
                )
        elif scope == NotificationScope.CATEGORY_DEFAULT:
            if self.category is None:
                raise ValueError(
                    "category es requerido para scope=category_default"
                )
            if (
                self.recurring_bill_id is not None
                or self.custom_event_id is not None
            ):
                raise ValueError(
                    "scope=category_default no admite recurring_bill_id ni custom_event_id"
                )
        elif scope == NotificationScope.GLOBAL_DEFAULT:
            if (
                self.recurring_bill_id is not None
                or self.custom_event_id is not None
                or self.category is not None
            ):
                raise ValueError(
                    "scope=global_default no admite bill/event/category"
                )

        for d in self.advance_days:
            if d < 0 or d > 365:
                raise ValueError("advance_days debe ser >= 0 y <= 365")
        # descending order required
        sorted_desc = sorted(set(self.advance_days), reverse=True)
        object.__setattr__(self, "advance_days", sorted_desc)
        return self


class NotificationRuleUpdate(BaseModel):
    advance_days: Optional[list[int]] = Field(None, min_length=1)
    is_active: Optional[bool] = None

    @model_validator(mode="after")
    def _validate(self):
        if self.advance_days is not None:
            for d in self.advance_days:
                if d < 0 or d > 365:
                    raise ValueError("advance_days debe ser >= 0 y <= 365")
            object.__setattr__(
                self, "advance_days", sorted(set(self.advance_days), reverse=True)
            )
        return self


class NotificationRuleResponse(BaseModel):
    id: uuid.UUID
    scope: str
    recurring_bill_id: Optional[uuid.UUID]
    custom_event_id: Optional[uuid.UUID]
    category: Optional[str]
    advance_days: list[int]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── notification_events (pending / acknowledge) ───────────────────────────────


class NotificationEventResponse(BaseModel):
    id: uuid.UUID
    bill_occurrence_id: Optional[uuid.UUID]
    custom_event_id: Optional[uuid.UUID]
    trigger_date: date
    advance_days: int
    channel: str
    status: str
    delivered_at: Optional[datetime]
    acknowledged_at: Optional[datetime]
    payload_snapshot: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── calendar/upcoming feed item ───────────────────────────────────────────────


class UpcomingFeedItem(BaseModel):
    """Polymorphic feed entry — either a bill_occurrence or a custom_event."""

    item_type: Literal["bill", "event"]
    id: uuid.UUID
    date: date
    title: str
    amount: Optional[float]
    currency: str
    status: Optional[str] = None
    category: Optional[str] = None
    provider: Optional[str] = None
    recurring_bill_id: Optional[uuid.UUID] = None
    is_overdue: bool = False


class UpcomingFeedResponse(BaseModel):
    items: list[UpcomingFeedItem]
    from_date: date
    to_date: date


# ── job responses ─────────────────────────────────────────────────────────────


class JobRunResult(BaseModel):
    ok: bool = True
    job: str
    processed: int
    created: int = 0
    updated: int = 0


class ChannelQuery(BaseModel):
    channel: Optional[NotificationChannel] = None
