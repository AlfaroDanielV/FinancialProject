"""add Costa Rica specific fields to debts table

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-16 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "debts",
        sa.Column("rate_type", sa.String(20), nullable=False, server_default="fixed"),
    )
    op.add_column(
        "debts",
        sa.Column("rate_reference", sa.String(50), nullable=True),
    )
    op.add_column(
        "debts",
        sa.Column("rate_spread", sa.Numeric(5, 4), nullable=True),
    )
    op.add_column(
        "debts",
        sa.Column(
            "prepayment_penalty_pct",
            sa.Numeric(5, 4),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "debts",
        sa.Column("payments_made", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "debts",
        sa.Column(
            "includes_insurance",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "debts",
        sa.Column("insurance_monthly", sa.Numeric(12, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("debts", "insurance_monthly")
    op.drop_column("debts", "includes_insurance")
    op.drop_column("debts", "payments_made")
    op.drop_column("debts", "prepayment_penalty_pct")
    op.drop_column("debts", "rate_spread")
    op.drop_column("debts", "rate_reference")
    op.drop_column("debts", "rate_type")
