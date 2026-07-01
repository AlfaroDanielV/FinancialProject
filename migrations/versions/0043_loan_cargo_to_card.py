"""loan cargo automático: debts.charge_to_account_id + source='loan_cargo'

A personal loan can be set to "cargo automático": its monthly cuota is charged
directly to a linked credit card (the user pays the card, never the loan from a
bank account). The link is a nullable FK on the OBLIGATION (one loan → ≤1 card;
one card → many loans; no join table), DISTINCT from the overloaded informational
`debts.account_id`.

`ON DELETE SET NULL`: hard-deleting the card detaches the loan, never deletes it.

The daily worker posts the cuota as a NEGATIVE transaction on the card carrying
its own `source='loan_cargo'` (so it's identifiable for the delete→undo coupling
and idempotency); it still counts as a card gasto. `transactions.source` has a
binding CHECK from migration 0011 (widened for apple_pay in 0039) — this widens
it again. No data change for existing rows.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043"
down_revision: Union[str, None] = "0042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SOURCE_CONSTRAINT = "ck_transactions_source"
_SOURCE_OLD = "source IN ('manual','shortcut','telegram','gmail','reconciled','apple_pay')"
_SOURCE_NEW = (
    "source IN ('manual','shortcut','telegram','gmail','reconciled','apple_pay',"
    "'loan_cargo')"
)


def upgrade() -> None:
    op.add_column(
        "debts",
        sa.Column(
            "charge_to_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Most loans are not card-linked; a partial index keeps the
    # "which loans charge to this card" lookup small.
    op.create_index(
        "ix_debts_charge_to_account",
        "debts",
        ["charge_to_account_id"],
        postgresql_where=sa.text("charge_to_account_id IS NOT NULL"),
    )

    op.drop_constraint(_SOURCE_CONSTRAINT, "transactions", type_="check")
    op.create_check_constraint(_SOURCE_CONSTRAINT, "transactions", _SOURCE_NEW)


def downgrade() -> None:
    op.drop_constraint(_SOURCE_CONSTRAINT, "transactions", type_="check")
    op.create_check_constraint(_SOURCE_CONSTRAINT, "transactions", _SOURCE_OLD)

    op.drop_index("ix_debts_charge_to_account", table_name="debts")
    op.drop_column("debts", "charge_to_account_id")
