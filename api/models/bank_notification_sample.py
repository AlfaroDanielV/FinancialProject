import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from .base import Base


class BankNotificationSample(Base):
    __tablename__ = "bank_notification_samples"
    __table_args__ = (
        CheckConstraint(
            "source IN ('photo','text')",
            name="ck_bank_notification_samples_source",
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
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    detected_sender: Mapped[Optional[str]] = mapped_column(
        String(320), nullable=True
    )
    detected_bank: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    detected_format: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )
    confidence: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(4, 3), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
