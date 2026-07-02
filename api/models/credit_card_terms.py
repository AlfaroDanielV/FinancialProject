"""Phase 7b — credit-card terms, 1:1 with a credit account (migration 0029).

Parameters only. There is deliberately NO balance column: the card's balance
is computed live from transactions via `compute_account_balances` — a stored
copy would drift. See vault `Decision - Credit Card Terms As Account
Parameters (Not A Debt)`.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TIMESTAMP

from .base import Base


class CreditCardTerms(Base):
    __tablename__ = "credit_card_terms"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # Annual purchase rate as a 0–1 fraction (18% → 0.18), like
    # debts.interest_rate.
    annual_interest_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False
    )
    cash_advance_rate: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 4), nullable=True
    )
    # CR cards: mínimo = max(pct × saldo, piso).
    minimum_payment_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False
    )
    minimum_payment_floor: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    credit_limit: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    # Día de corte (statement) / fecha límite de pago.
    statement_day: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    payment_due_day: Mapped[int] = mapped_column(Integer, nullable=False)
    # The real next payment date the user confirmed at creation (e.g. a card
    # opened Jul 1 with due day 28 pays Jul 28, not the phantom Jun 28 corte).
    # The projected due is clamped to max(natural_due, first_due_date), so no
    # obligation is ever surfaced before the card actually had a statement.
    # NULL → behave exactly as before (pure statement-cycle projection).
    first_due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # Recurring-payment preference: 'minimum' (the must-pay floor) or 'full'
    # (pago de contado — the full live balance). Selects which LIVE figure the
    # upcoming-payment projection surfaces; never a stored amount, never a
    # materialized bill. Budget/affordability/reservation stay on the minimum.
    payment_mode: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="minimum"
    )
    # B5 obligation attachment — the card minimum reserves inside this
    # envelope while unpaid. ON DELETE SET NULL (deleting an envelope
    # detaches, never deletes the terms).
    envelope_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("envelopes.id", ondelete="SET NULL"),
        nullable=True,
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    account: Mapped["Account"] = relationship()  # noqa: F821
