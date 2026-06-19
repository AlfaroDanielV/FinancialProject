import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import CheckConstraint, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from .base import Base


class AccountAnchor(Base):
    """Append-only bank-reconciliation anchor for an account.

    The user's real bank balance is the source of truth; transactions are
    explanatory. Balance is projected forward from the latest anchor::

        balance = latest_anchor.value
                  + Σ(confirmed, non-archived txns with
                      transaction_date > effective_date)

    NEVER mutated — a re-anchor APPENDS a new row (decisions #1/#6). The latest
    anchor for an account is the row with the greatest
    ``(effective_date, created_at)``. ``effective_date`` is day-level (rule #6):
    a txn dated ON the anchor day is pre-anchor (strict ``>``), because it is
    already reflected in the balance the user read.

    See docs/balance-anchor-reconciliation-decisions.md.
    """

    __tablename__ = "account_anchors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The stated real balance at `effective_date`. Numeric(14,2) matches
    # `accounts.initial_balance`. Read/written as Decimal, never float.
    value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="CRC"
    )
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    # onboarding | reanchor | migrated | statement  (CHECK below)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "source IN ('onboarding','reanchor','migrated','statement')",
            name="ck_account_anchors_source",
        ),
    )
