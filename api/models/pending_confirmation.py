import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from .base import Base


class PendingConfirmation(Base):
    __tablename__ = "pending_confirmations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    short_id: Mapped[str] = mapped_column(String(16), nullable=False)
    channel: Mapped[str] = mapped_column(
        String(20), nullable=False, default="telegram"
    )
    channel_message_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    proposed_action: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    resolution: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
