import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import Boolean, ForeignKey, Numeric, SmallInteger, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from .base import Base


class EnvelopeSnapshot(Base):
    """Frozen per-period copy of one envelope's execution figures (Phase 7e).

    The live summary recomputes from current limits and transactions, so "did
    I respect my budget in March" becomes unanswerable once a limit is edited.
    The nightly job upserts the current period (and recaptures the just-closed
    period during the first days of a month); identity fields are denormalized
    so history survives an envelope hard delete (FK SET NULL)."""

    __tablename__ = "envelope_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # "YYYY-MM"
    envelope_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("envelopes.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    envelope_class: Mapped[str] = mapped_column(String(16), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    depth: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    limit_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    spent: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    direct_spent: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    reserved: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    available: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    over_limit: Mapped[bool] = mapped_column(Boolean, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )


class CashflowSnapshot(Base):
    """Frozen per-period MonthlyCashflow picture + envelope grand totals
    (Phase 7e). UNIQUE(user_id, period); the nightly job upserts. `payload`
    carries the full dump (by_class, unattached obligations) for
    reproducibility."""

    __tablename__ = "cashflow_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    monthly_income: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    debt_payments: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    recurring_bills: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    envelope_allocations: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False
    )
    committed_outflows: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False
    )
    surplus: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    savings_allocations: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False
    )
    gate_reason: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    total_limit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    total_spent: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    total_reserved: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    total_available: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
