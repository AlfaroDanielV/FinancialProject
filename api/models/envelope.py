import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Numeric, SmallInteger, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TIMESTAMP

from .base import Base


class Envelope(Base):
    """Spending-cap envelope ("sobre"). User-named bucket tied to a class
    (needs/wants/savings/investing) with a monthly spending limit. Spend is
    computed live from transactions (no stored running balance), so there is
    no `current_amount` column — see api/services/envelopes.py.

    Phase 7a: envelopes nest up to 5 levels via `parent_id`. A child inherits
    the root's class + currency; the parent's limit is its total budget and
    children are sub-allocations of it. Spend rolls up the tree at query time
    (still live, never stored)."""

    __tablename__ = "envelopes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Self-referential nesting (Phase 7a). ON DELETE CASCADE: hard-deleting a
    # parent removes its subtree; tagged transactions unlink via their own
    # envelope_id SET NULL FK.
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("envelopes.id", ondelete="CASCADE"),
        nullable=True,
    )
    # 1-based; root = 1, max 5 (CHECK in migration 0024).
    depth: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # needs | wants | savings | investing (CHECK in migration 0022)
    envelope_class: Mapped[str] = mapped_column(String(16), nullable=False)
    limit_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CRC")
    period: Mapped[str] = mapped_column(String(16), nullable=False, default="monthly")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user: Mapped["User"] = relationship()  # noqa: F821
    children: Mapped[list["Envelope"]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    parent: Mapped[Optional["Envelope"]] = relationship(
        back_populates="children", remote_side="Envelope.id"
    )
