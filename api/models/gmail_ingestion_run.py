import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from .base import Base


class GmailIngestionRun(Base):
    __tablename__ = "gmail_ingestion_runs"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('backfill','daily','manual')",
            name="ck_gmail_ingestion_runs_mode",
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
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    messages_scanned: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    transactions_created: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    transactions_matched: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    errors: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
