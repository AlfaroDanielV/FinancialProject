import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from .base import Base


class LazyDetectionEvent(Base):
    __tablename__ = "lazy_detection_events"
    __table_args__ = (
        CheckConstraint(
            "hint_type IN ('account','bank','debt')",
            name="ck_lazy_detection_events_hint_type",
        ),
        CheckConstraint(
            "resolution IN ('created','linked_existing','dismissed','pending')",
            name="ck_lazy_detection_events_resolution",
        ),
        CheckConstraint(
            "fuzzy_match_score IS NULL "
            "OR (fuzzy_match_score >= 0 AND fuzzy_match_score <= 1)",
            name="ck_lazy_detection_events_score_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # account | bank | debt
    hint_type: Mapped[str] = mapped_column(String(16), nullable=False)
    hint_text: Mapped[str] = mapped_column(Text, nullable=False)
    fuzzy_match_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(3, 2), nullable=True
    )
    # created | linked_existing | dismissed | pending
    resolution: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    matched_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
