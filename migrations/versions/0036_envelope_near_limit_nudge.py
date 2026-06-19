"""envelope_near_limit nudge type

Revision ID: 0036
Revises: 0035
Create Date: 2026-06-19 00:00:00.000000

The envelope-near-limit evaluator raises an `envelope_near_limit` nudge so the
user is alerted when a spending-cap envelope is nearly spent (and again if it
goes over its limit), delivered via Telegram + the in-app Alertas feed. Both
user_nudges.nudge_type and user_nudge_silences.nudge_type carry a CHECK
enumerating the allowed types (Phase 5d, migration 0008; widened in 0023, 0033).
This migration adds 'envelope_near_limit' to both. No data change.

Sits on top of 0035 (account_anchors) — claimed 0036 after a 0035 collision
with that in-flight migration (lesson: claim migration numbers against the
working tree, not just committed history).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD = (
    "nudge_type IN ('missing_income','stale_pending_confirmation',"
    "'upcoming_bill','over_commitment','duplicate_transaction')"
)
_NEW = (
    "nudge_type IN ('missing_income','stale_pending_confirmation',"
    "'upcoming_bill','over_commitment','duplicate_transaction',"
    "'envelope_near_limit')"
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
