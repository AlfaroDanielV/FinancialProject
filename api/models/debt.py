import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, Integer, Numeric, String, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TIMESTAMP

from .base import Base


class Debt(Base):
    __tablename__ = "debts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # mortgage | credit_card | auto_loan | personal_loan | student_loan | other
    debt_type: Mapped[str] = mapped_column(String(50), nullable=False)
    lender: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    original_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    current_balance: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    # Annual rate as decimal, e.g. 0.0850 = 8.50%
    interest_rate: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    minimum_payment: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    payment_due_day: Mapped[int] = mapped_column(Integer, nullable=False)
    term_months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    maturity_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CRC")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # fixed | variable
    rate_type: Mapped[str] = mapped_column(String(20), nullable=False, default="fixed")
    # Reference rate name for variable rates: "TBP", "PRIME", "SOFR"
    rate_reference: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Spread over reference rate as decimal (e.g. 0.0500 = TBP + 5%)
    rate_spread: Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)
    # Early payoff commission as decimal (e.g. 0.03 = 3%). Ley 7472: prohibited after 2 payments
    prepayment_penalty_pct: Mapped[float] = mapped_column(
        Numeric(5, 4), nullable=False, default=0
    )
    # Number of payments already made (used to determine if prepayment penalty applies)
    payments_made: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Whether monthly payment includes bundled insurance (common in CR mortgages)
    includes_insurance: Mapped[bool] = mapped_column(Boolean, default=False)
    # Monthly insurance amount if bundled
    insurance_monthly: Mapped[Optional[float]] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user: Mapped["User"] = relationship(back_populates="debts")  # noqa: F821
    account: Mapped[Optional["Account"]] = relationship()  # noqa: F821
    payments: Mapped[list["DebtPayment"]] = relationship(
        back_populates="debt", order_by="DebtPayment.payment_date.desc()"
    )


class DebtPayment(Base):
    __tablename__ = "debt_payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    debt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("debts.id"), nullable=False
    )
    transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True
    )
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_paid: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    principal_portion: Mapped[Optional[float]] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    interest_portion: Mapped[Optional[float]] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    extra_payment: Mapped[Optional[float]] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    remaining_balance: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )

    debt: Mapped["Debt"] = relationship(back_populates="payments")
