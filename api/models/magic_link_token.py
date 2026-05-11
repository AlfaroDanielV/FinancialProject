import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from .base import Base


class MagicLinkToken(Base):
    __tablename__ = "magic_link_tokens"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('onboarding','edit_session')",
            name="ck_magic_link_tokens_purpose",
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
    # Plain part of the raw token (`<selector>.<verifier>`). Indexed UNIQUE
    # for O(1) lookup before the bcrypt verify step (Resolución 9.9).
    selector: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True
    )
    # bcrypt hash of the verifier portion.
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # JWT ID issued in the post-exchange session cookie (Resolución 9.1).
    jti: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True
    )
    # onboarding | edit_session
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    used_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
