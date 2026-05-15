"""phase 6e b5: transactions soft-delete column

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-12 00:00:00.000000

Adds a transactions.archived column for the global Transactions module's
bulk-archive workflow. Orthogonal to `status` (confirmed/shadow/pending_review).

Adds a partial index `(user_id, transaction_date desc) WHERE archived=false`
to keep the hot list path fast as transaction count grows.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column(
            "archived",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_transactions_user_date_active",
        "transactions",
        ["user_id", "transaction_date"],
        postgresql_where=sa.text("archived = false"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_transactions_user_date_active",
        table_name="transactions",
    )
    op.drop_column("transactions", "archived")
