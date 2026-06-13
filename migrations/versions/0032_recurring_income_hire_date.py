"""Add recurring_incomes.hire_date (fecha de incorporación).

Drives the CR aguinaldo + salario-escolar proration: the amounts are computed
from the salary's monthly gross and the number of days worked in the accrual
window. Nullable — unknown hire date assumes the full window (one month's
gross for aguinaldo). Only salary rows carry it.

NOTE: this migration does NOT rewrite existing income `amount` values. After
the frequency-division change, a salary stores the PER-PAYMENT amount; rows
created before that change stored the monthly figure with a non-monthly
cadence and would now read inflated. Re-enter those incomes, or run the opt-in
`scripts/backfill_income_per_payment.py` once.

Revision ID: 0032
Revises: 0031
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "recurring_incomes",
        sa.Column("hire_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recurring_incomes", "hire_date")
