"""phase 7b: debt_payments + bill_occurrences transaction FKs → ON DELETE SET NULL

Revision ID: 0028
Revises: 0027
Create Date: 2026-06-11 00:00:00.000000

Account hard-delete (Phase 7b B2) cascades the account's transactions. Payment
and occurrence HISTORY must survive that — the row keeps its amounts/dates and
only loses the transaction link — so the two NO-ACTION FKs into `transactions`
are aligned with the convention `goal_contributions` and `gmail_messages_seen`
already follow (SET NULL).

This also fixes a latent bug independent of hard delete: `bot/undo.py`
hard-deletes the last telegram transaction, and a row referenced by a
debt_payment or bill_occurrence would have raised an FK violation.

No data change; both columns were already nullable.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, default-named FK constraint, referenced table)
_FKS = (
    ("debt_payments", "debt_payments_transaction_id_fkey"),
    ("bill_occurrences", "bill_occurrences_transaction_id_fkey"),
)


def upgrade() -> None:
    for table, constraint in _FKS:
        op.drop_constraint(constraint, table, type_="foreignkey")
        op.create_foreign_key(
            constraint,
            table,
            "transactions",
            ["transaction_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    for table, constraint in reversed(_FKS):
        op.drop_constraint(constraint, table, type_="foreignkey")
        op.create_foreign_key(
            constraint,
            table,
            "transactions",
            ["transaction_id"],
            ["id"],
        )
