"""accounts/debts: statement-reconciliation identity columns

Revision ID: 0038
Revises: 0037
Create Date: 2026-06-26 00:00:00.000000

The generalized statement reconciliation resolves a statement product to a ledger
account/debt by identifier priority (IBAN → account_number → last4) before falling
back to issuer+product fuzzy matching. These nullable columns hold those
identifiers; they are self-stamped (fill-if-null) the first time a statement leg
resolves to the account/debt, so the NEXT statement matches deterministically.

Additive, nullable, no backfill — resolution degrades to fuzzy when null, exactly
the pre-0038 behavior.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: Union[str, None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("accounts", "debts"):
        op.add_column(table, sa.Column("iban", sa.String(length=40), nullable=True))
        op.add_column(
            table, sa.Column("account_number", sa.String(length=64), nullable=True)
        )
        op.add_column(table, sa.Column("last4", sa.String(length=8), nullable=True))


def downgrade() -> None:
    for table in ("accounts", "debts"):
        op.drop_column(table, "last4")
        op.drop_column(table, "account_number")
        op.drop_column(table, "iban")
