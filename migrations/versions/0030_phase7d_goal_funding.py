"""phase 7d: goal funding from accounts

Revision ID: 0030
Revises: 0029
Create Date: 2026-06-11 00:00:00.000000

Goal contributions become real money movements (vault `Decision - Goal
Funding From Accounts`):

- `transactions.goal_id` — marks a goal aporte (negative) or refund
  (positive). Mirror of `transfer_id`: these rows count toward account
  balances but are EXCLUDED from income/expense analytics (saving isn't
  spending) and are machine-managed (PATCH/bulk 409). ON DELETE SET NULL —
  deleting a goal keeps the account history honest.
- `goal_contributions.source_account_id` — where the aporte came from, so a
  cancel can refund to the same account. SET NULL: hard-deleting the account
  degrades the contribution to tracking-only (unrefundable), never an error.
- `goal_contributions.refund_transaction_id` — the refund idempotency stamp.
  A contribution is refundable iff source_account_id IS NOT NULL AND
  refund_transaction_id IS NULL. Keeps the CHECK amount > 0 intact (no
  negative contribution rows).

No data backfill: historical contributions stay sourceless = tracking-only.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_transactions_goal_id",
        "transactions",
        ["goal_id"],
        postgresql_where=sa.text("goal_id IS NOT NULL"),
    )
    op.add_column(
        "goal_contributions",
        sa.Column(
            "source_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "goal_contributions",
        sa.Column(
            "refund_transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("goal_contributions", "refund_transaction_id")
    op.drop_column("goal_contributions", "source_account_id")
    op.drop_index("ix_transactions_goal_id", table_name="transactions")
    op.drop_column("transactions", "goal_id")
