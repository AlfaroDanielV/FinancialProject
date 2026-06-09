import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TIMESTAMP

from .base import Base


class RecurringIncome(Base):
    __tablename__ = "recurring_incomes"
    __table_args__ = (
        CheckConstraint(
            "income_type IN ('salary','aguinaldo','salario_escolar',"
            "'freelance','other')",
            name="ck_recurring_incomes_type",
        ),
        CheckConstraint(
            "frequency IN ('weekly','biweekly','monthly','annual')",
            name="ck_recurring_incomes_frequency",
        ),
        CheckConstraint(
            "income_type NOT IN ('aguinaldo','salario_escolar') "
            "OR base_salary_link_id IS NOT NULL",
            name="ck_recurring_incomes_cr_cycles_need_base",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # salary | aguinaldo | salario_escolar | freelance | other
    income_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # NULL allowed: aguinaldo / salario_escolar derive amount from base salary
    amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    # For salary incomes captured as gross via the CR calculator: the
    # pre-deduction monthly figure. `amount` stays the NET take-home; this keeps
    # the gross so the Ingresos edit can re-show it and recompute the net.
    gross_monthly: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="CRC"
    )
    # weekly | biweekly | monthly | annual
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    next_payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    base_salary_link_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recurring_incomes.id", ondelete="CASCADE"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    base_salary: Mapped[Optional["RecurringIncome"]] = relationship(
        "RecurringIncome", remote_side=[id]
    )
