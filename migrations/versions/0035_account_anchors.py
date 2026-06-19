"""account_anchors: bank-reconciliation anchor (balance source of truth)

Revision ID: 0035
Revises: 0034
Create Date: 2026-06-18 00:00:00.000000

Inverts the account balance from bottom-up reconstruction to anchor-based
reconciliation: the user's real bank balance is the anchor, transactions are
explanatory, and balance is projected forward as
``latest_anchor.value + Σ(txns with transaction_date > effective_date)``.

Append-only — a re-anchor APPENDS a new row (never mutates a running balance).

Behavior-preserving backfill: one ``'migrated'`` anchor per existing account so
the new formula (A5) reproduces today's ``initial_balance + Σ(all confirmed)``
numbers BYTE-FOR-BYTE; the cutover changes no displayed number until a user
re-anchors. The trick is ``effective_date = MIN(transaction_date) - 1 day`` per
account (fallback ``created_at::date`` when the account has no transactions) —
every existing row is then strictly AFTER the anchor (A1's strict ``>``). Using
``created_at::date`` would have silently dropped same-day / pre-creation-dated
rows on cutover (operator catch).

See docs/balance-anchor-reconciliation-decisions.md (Block 2 / Gate C).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035"
down_revision: Union[str, None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "account_anchors",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("value", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "currency", sa.String(3), nullable=False, server_default="CRC"
        ),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "source IN ('onboarding','reanchor','migrated')",
            name="ck_account_anchors_source",
        ),
    )
    # "Latest anchor per account" lookup — supports the DISTINCT ON
    # (account_id) ORDER BY effective_date DESC, created_at DESC read (Postgres
    # scans this composite btree backwards). Plain ASC index is sufficient.
    op.create_index(
        "ix_account_anchors_latest",
        "account_anchors",
        ["account_id", "effective_date", "created_at"],
    )

    # Behavior-preserving backfill: one 'migrated' anchor per existing account.
    op.execute(
        """
        INSERT INTO account_anchors
            (id, user_id, account_id, value, currency,
             effective_date, source, created_at)
        SELECT
            gen_random_uuid(),
            a.user_id,
            a.id,
            COALESCE(a.initial_balance, 0),
            a.currency,
            COALESCE(
                (SELECT MIN(t.transaction_date) - 1
                   FROM transactions t
                  WHERE t.account_id = a.id),
                a.created_at::date
            ),
            'migrated',
            a.created_at
        FROM accounts a
        """
    )


def downgrade() -> None:
    op.drop_index("ix_account_anchors_latest", table_name="account_anchors")
    op.drop_table("account_anchors")
