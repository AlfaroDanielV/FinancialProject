import uuid
from datetime import datetime

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TIMESTAMP

from .base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CRC")
    timezone: Mapped[str] = mapped_column(
        String(50), nullable=False, default="America/Costa_Rica"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )

    accounts: Mapped[list["Account"]] = relationship(back_populates="user")  # noqa: F821
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="user")  # noqa: F821
    budgets: Mapped[list["Budget"]] = relationship(back_populates="user")  # noqa: F821
    goals: Mapped[list["Goal"]] = relationship(back_populates="user")  # noqa: F821
    events: Mapped[list["Event"]] = relationship(back_populates="user")  # noqa: F821
    weekly_reports: Mapped[list["WeeklyReport"]] = relationship(back_populates="user")  # noqa: F821
