"""transactions: allow source='apple_pay'

Revision ID: 0039
Revises: 0038
Create Date: 2026-06-25 00:00:00.000000

Apple Pay zero-touch capture (iOS App Intent on the Wallet/Transaction trigger)
writes a transaction at NFC-tap time. It is a distinct origin from the existing
sources, so it carries its own `source='apple_pay'` value (the reconciler +
balance/idempotency logic filter on it). `transactions.source` has a binding
CHECK from migration 0011; this widens it to include 'apple_pay'. No data
change.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0039"
down_revision: Union[str, None] = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CONSTRAINT = "ck_transactions_source"
_OLD = "source IN ('manual','shortcut','telegram','gmail','reconciled')"
_NEW = "source IN ('manual','shortcut','telegram','gmail','reconciled','apple_pay')"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "transactions", type_="check")
    op.create_check_constraint(_CONSTRAINT, "transactions", _NEW)


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "transactions", type_="check")
    op.create_check_constraint(_CONSTRAINT, "transactions", _OLD)
