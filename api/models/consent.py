import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from .base import Base


class UserConsent(Base):
    """Append-only consent ledger (Phase 7e). One row per grant/revoke act;
    the user's current state for a purpose is the LATEST row by occurred_at.
    Rows are never updated or deleted (audit trail) — except by the
    user-level CASCADE when an account is erased, which is itself the
    GDPR/Ley 8968 erasure path.

    Purposes (CHECK-constrained in migration 0030; widen by migration):
      core_service         — operate the product for the user
      behavioral_insights  — Phase 6c profiling (computed + extracted)
      product_research     — internal analysis to improve the product
      aggregated_datasets  — inclusion in k-anonymous aggregate datasets
    """

    __tablename__ = "user_consents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="api")
    occurred_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
