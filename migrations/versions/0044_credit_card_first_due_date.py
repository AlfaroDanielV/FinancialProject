"""credit_card_terms: add first_due_date (clamp phantom mid-cycle overdue)

Revision ID: 0044
Revises: 0043
Create Date: 2026-07-01 12:00:00.000000

A credit card opened mid-cycle (e.g. Jul 1, due day 28) projected a phantom
Jun 28 "overdue" payment because `last_corte` can land on a corte that
predates the account and the debt leaks backward via `balance_as_of`'s
initial_balance fallback. `first_due_date` stores the real next payment date
the user confirms at creation; the projection clamps its due to
max(natural_due, first_due_date), so nothing is surfaced before the card
actually had a statement. Nullable — existing cards keep NULL and behave
exactly as before. See vault `Decision - Credit Card Terms As Account
Parameters (Not A Debt)`.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: Union[str, None] = "0043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "credit_card_terms",
        sa.Column("first_due_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("credit_card_terms", "first_due_date")
