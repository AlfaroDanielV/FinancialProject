"""phase 6b addenda: gmail_sender_whitelist

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-06 00:00:00.000000

What this does:
    Creates `gmail_sender_whitelist` — per-user list of email senders the
    Gmail scanner is allowed to filter on. Append-only by convention:
    `/quitar_banco` sets `removed_at=now()` instead of deleting rows, so
    we keep an audit trail of what was scanned historically (relevant
    when a stray transaction is debugged later).

    Also: a partial unique index on (user_id, sender_email) WHERE
    removed_at IS NULL — guarantees no two ACTIVE rows for the same
    (user, sender). A user who removes and re-adds the same email gets
    a soft un-delete via add_sender's upsert, not a duplicate row.

Out of scope: backfilling existing samples into the whitelist. Migrating
`bank_notification_samples.detected_sender` rows here would couple two
concepts that aren't equivalent (a sample from onboarding ≠ a sender
the scanner should query). Daniel's existing user `8df6bbdd-...` keeps
its sample row and starts the new flow from /agregar_banco fresh.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gmail_sender_whitelist",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Email is stored lowercased by app code. We don't use citext to
        # avoid yet another extension; the lowercasing is enforced at the
        # service boundary (whitelist.add_sender).
        sa.Column("sender_email", sa.Text(), nullable=False),
        sa.Column("bank_name", sa.Text(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column(
            "added_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "removed_at", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.CheckConstraint(
            "source IN ('preset_tap','custom_typed','imported')",
            name="ck_gmail_sender_whitelist_source",
        ),
    )
    # Partial UNIQUE: only one active row per (user, sender). Removed
    # rows can coexist with their active replacements when the user
    # re-adds the same email after removal — but the service uses a
    # raw upsert that nullifies removed_at instead, so this index also
    # never has to deal with that case in practice.
    op.create_index(
        "uq_gmail_sender_whitelist_active",
        "gmail_sender_whitelist",
        ["user_id", "sender_email"],
        unique=True,
        postgresql_where=sa.text("removed_at IS NULL"),
    )
    # Hot path: scanner reads list_active(user_id), so a non-unique
    # partial index on (user_id) WHERE removed_at IS NULL would be
    # tempting — but the unique index above already covers it. Skip.
    op.create_index(
        "ix_gmail_sender_whitelist_user",
        "gmail_sender_whitelist",
        ["user_id", sa.text("added_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_gmail_sender_whitelist_user", table_name="gmail_sender_whitelist"
    )
    op.drop_index(
        "uq_gmail_sender_whitelist_active",
        table_name="gmail_sender_whitelist",
    )
    op.drop_table("gmail_sender_whitelist")
