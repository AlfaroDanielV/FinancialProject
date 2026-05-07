import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from .base import Base


class GmailMessageSeen(Base):
    __tablename__ = "gmail_messages_seen"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('matched','created','created_shadow',"
            "'skipped','failed','rejected_by_user')",
            name="ck_gmail_messages_seen_outcome",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    gmail_message_id: Mapped[str] = mapped_column(
        String(128), primary_key=True
    )
    processed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    ingestion_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gmail_ingestion_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    error: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
