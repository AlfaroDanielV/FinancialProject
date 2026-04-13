import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import String, Boolean, Numeric, Date, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TIMESTAMP, Integer

from .base import Base


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # birthday | anniversary | maintenance | trip | subscription
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    estimated_cost: Mapped[Optional[float]] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    # yearly | monthly | quarterly
    recurrence_rule: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    alert_days_before: Mapped[Optional[list]] = mapped_column(
        ARRAY(Integer), default=lambda: [30, 14, 7]
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )

    user: Mapped["User"] = relationship(back_populates="events")  # noqa: F821
