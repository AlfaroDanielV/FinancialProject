"""duplicate_transaction nudge type

Revision ID: 0033
Revises: 0032
Create Date: 2026-06-15 00:00:00.000000

Duplicate-transaction detection raises a `duplicate_transaction` nudge so the
user can keep or delete a likely-duplicate expense (Telegram + in-app Alertas).
Both user_nudges.nudge_type and user_nudge_silences.nudge_type carry a CHECK
enumerating the allowed types (Phase 5d, migration 0008; widened in 0023). This
migration adds 'duplicate_transaction' to both. No data change.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD = (
    "nudge_type IN ('missing_income','stale_pending_confirmation',"
    "'upcoming_bill','over_commitment')"
)
_NEW = (
    "nudge_type IN ('missing_income','stale_pending_confirmation',"
    "'upcoming_bill','over_commitment','duplicate_transaction')"
)

_TARGETS = (
    ("user_nudges", "ck_user_nudges_nudge_type"),
    ("user_nudge_silences", "ck_user_nudge_silences_nudge_type"),
)


def upgrade() -> None:
    for table, constraint in _TARGETS:
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(constraint, table, _NEW)


def downgrade() -> None:
    for table, constraint in _TARGETS:
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(constraint, table, _OLD)
