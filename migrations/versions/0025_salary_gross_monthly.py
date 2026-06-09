"""Salary capture: store the gross monthly figure on recurring_incomes.

For salary incomes captured as gross, `amount` stays the NET (the actual
take-home that drives budgets / affordability) and `gross_monthly` keeps the
pre-deduction figure so the Ingresos edit can re-show the gross and recompute
the net. Nullable — only salary incomes captured via the calculator set it.

Revision ID: 0025
Revises: 0024
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "recurring_incomes",
        sa.Column("gross_monthly", sa.Numeric(14, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recurring_incomes", "gross_monthly")
