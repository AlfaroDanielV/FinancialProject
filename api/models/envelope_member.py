import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from .base import Base


class EnvelopeMember(Base):
    """A user invited to a shared envelope by its owner (shared envelopes).

    Membership rows exist ONLY for invited members — the owner is implicit via
    `envelopes.user_id`. Only ROOT envelopes are shareable; access cascades to the
    subtree. A member may add/remove only their OWN expenses to/from the shared
    envelope, and sees only the aggregate spend + their own portion — never
    another member's transaction lines. The shared envelope NEVER counts toward a
    member's own budget totals (cashflow/affordability/snapshots filter it out).

    Cap: at most 9 invited members per root (≤ 10 people incl. the owner). See
    api/services/envelope_sharing.py.
    """

    __tablename__ = "envelope_members"
    __table_args__ = (
        UniqueConstraint(
            "envelope_id", "user_id", name="uq_envelope_members_envelope_user"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # The shared ROOT envelope. CASCADE: deleting the envelope drops memberships;
    # the member's tagged transactions unlink via their own envelope_id SET NULL.
    envelope_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("envelopes.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The invited member.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # The owner who shared it (audit).
    shared_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
