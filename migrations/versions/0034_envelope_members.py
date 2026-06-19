"""shared envelopes: envelope_members table

Revision ID: 0034
Revises: 0033
Create Date: 2026-06-15 00:00:00.000000

Lets an envelope OWNER share a ROOT envelope (and, via the subtree, its
sub-sobres) with up to 9 other users. Each member can add/remove only their OWN
expenses to/from the shared envelope; the spend bar is a shared cap (drains with
the combined spend of all members). A membership row exists ONLY for an invited
member — the owner is implicit via `envelopes.user_id`.

- `envelope_id` ON DELETE CASCADE: hard-deleting (or cascade-deleting) the
  envelope removes its membership rows. The member's tagged transactions unlink
  via the existing `transactions.envelope_id … ON DELETE SET NULL` (0022) — they
  are never deleted.
- `user_id` / `shared_by` ON DELETE CASCADE: deleting a user removes their
  memberships. (The owner's envelopes cascade-delete first, so `shared_by` rarely
  fires independently; CASCADE keeps the table clean regardless.)
- `UNIQUE(envelope_id, user_id)` makes redeeming a share code idempotent — a
  second redeem of the same code by the same user is a no-op, not a duplicate.

No data change for existing rows. See vault
`Decision - Shared Household Envelopes (Deferred P8)` (status flipped) and
`api/services/envelope_sharing.py`.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "envelope_members",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "envelope_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("envelopes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The owner who shared the envelope (audit). Cascades with the user.
        sa.Column(
            "shared_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Idempotent redeem: a user can be a member of an envelope at most once.
        sa.UniqueConstraint(
            "envelope_id", "user_id", name="uq_envelope_members_envelope_user"
        ),
    )
    # "Shared with me" lookups filter on user_id.
    op.create_index(
        "ix_envelope_members_user", "envelope_members", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_envelope_members_user", table_name="envelope_members")
    op.drop_table("envelope_members")
