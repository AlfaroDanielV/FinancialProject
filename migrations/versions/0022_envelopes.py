"""envelope budgeting (Sobres): envelopes table + transactions.envelope_id

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-05 00:00:00.000000

Spending-cap envelopes on the native home tab: user-named buckets, each tied to
a class (needs/wants/savings/investing) with a monthly spending limit. Spend is
computed live from transactions (no stored running balance), so this only adds
config + the per-transaction link. See vault
`Decision - Envelope Budgeting - Spending Caps`.

`transactions.envelope_id` is nullable (most rows have no envelope) and ON
DELETE SET NULL — deleting an envelope must not delete its transactions, just
unlink them.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "envelopes",
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
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("envelope_class", sa.String(16), nullable=False),
        sa.Column("limit_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "currency", sa.String(3), nullable=False, server_default="CRC"
        ),
        sa.Column(
            "period", sa.String(16), nullable=False, server_default="monthly"
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "archived",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "envelope_class IN ('needs','wants','savings','investing')",
            name="ck_envelopes_class",
        ),
        sa.CheckConstraint(
            "limit_amount > 0", name="ck_envelopes_limit_positive"
        ),
    )
    # Active-envelope lookups (the home summary) filter user_id + not archived.
    op.create_index(
        "ix_envelopes_user_active",
        "envelopes",
        ["user_id"],
        postgresql_where=sa.text("archived = false"),
    )

    op.add_column(
        "transactions",
        sa.Column(
            "envelope_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("envelopes.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_transactions_envelope",
        "transactions",
        ["envelope_id"],
        postgresql_where=sa.text("envelope_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_envelope", table_name="transactions")
    op.drop_column("transactions", "envelope_id")
    op.drop_index("ix_envelopes_user_active", table_name="envelopes")
    op.drop_table("envelopes")
