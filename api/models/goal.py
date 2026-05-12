import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import String, Integer, Numeric, Date, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TIMESTAMP

from .base import Base


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    target_currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="CRC"
    )
    current_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    target_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    monthly_contribution: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    # active | achieved | abandoned | paused
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    linked_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )

    user: Mapped["User"] = relationship(back_populates="goals")  # noqa: F821
    linked_account: Mapped[Optional["Account"]] = relationship()  # noqa: F821
    contributions: Mapped[list["GoalContribution"]] = relationship(  # noqa: F821
        back_populates="goal", cascade="all, delete-orphan"
    )

    @property
    def deadline(self) -> Optional[date]:
        """Legacy name kept for older API callers; 6e uses target_date."""
        return self.target_date

    @deadline.setter
    def deadline(self, value: Optional[date]) -> None:
        self.target_date = value
