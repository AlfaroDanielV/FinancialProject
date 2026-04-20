import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TIMESTAMP

from .base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','suspended')", name="ck_users_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="CR")
    timezone: Mapped[str] = mapped_column(
        String(50), nullable=False, default="America/Costa_Rica"
    )
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CRC")
    locale: Mapped[str] = mapped_column(String(10), nullable=False, default="es-CR")
    shortcut_token: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True
    )
    telegram_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True, unique=True
    )
    whatsapp_phone: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, unique=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    accounts: Mapped[list["Account"]] = relationship(back_populates="user")  # noqa: F821
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="user")  # noqa: F821
    budgets: Mapped[list["Budget"]] = relationship(back_populates="user")  # noqa: F821
    goals: Mapped[list["Goal"]] = relationship(back_populates="user")  # noqa: F821
    weekly_reports: Mapped[list["WeeklyReport"]] = relationship(back_populates="user")  # noqa: F821
    debts: Mapped[list["Debt"]] = relationship(back_populates="user")  # noqa: F821
