import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import String, Boolean, Numeric, Date, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TIMESTAMP

from .base import Base


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=True
    )
    # negative = expense, positive = income
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CRC")
    merchant: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    subcategory: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    # manual | shortcut | telegram | gmail | reconciled (CHECK in 0011)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    # generic external ref for dedup. Phase 6b uses gmail_message_id below
    # for Gmail-origin rows; source_ref stays free for future origins.
    source_ref: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Phase 6b dedup column. UNIQUE(user_id, gmail_message_id) partial index
    # in migration 0011 prevents the scanner from double-inserting.
    gmail_message_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    parse_status: Mapped[str] = mapped_column(String(50), default="confirmed")
    # Phase 6b: confirmed | shadow | pending_review (CHECK in 0011). Shadow
    # rows do NOT count toward balance until /aprobar_shadow promotes them.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="confirmed"
    )
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )

    user: Mapped["User"] = relationship(back_populates="transactions")  # noqa: F821
    account: Mapped[Optional["Account"]] = relationship(back_populates="transactions")  # noqa: F821
