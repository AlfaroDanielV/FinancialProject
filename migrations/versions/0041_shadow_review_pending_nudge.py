"""shadow_review_pending nudge type

Revision ID: 0041
Revises: 0040
Create Date: 2026-06-25 00:00:00.000000

The shadow_review_pending evaluator reminds a user about Gmail-parsed
transactions left in shadow status (status='shadow', source='gmail') and
unreviewed for >= N days, re-reminding periodically until they're reviewed,
delivered via Telegram + the in-app Alertas feed. Both user_nudges.nudge_type
and user_nudge_silences.nudge_type carry a CHECK enumerating the allowed types
(Phase 5d, migration 0008; widened in 0023, 0033, 0036). This migration adds
'shadow_review_pending' to both. No data change.

Sits on top of 0040 (credit_card_payment_mode).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0041"
down_revision: Union[str, None] = "0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD = (
    "nudge_type IN ('missing_income','stale_pending_confirmation',"
    "'upcoming_bill','over_commitment','duplicate_transaction',"
    "'envelope_near_limit')"
)
_NEW = (
    "nudge_type IN ('missing_income','stale_pending_confirmation',"
    "'upcoming_bill','over_commitment','duplicate_transaction',"
    "'envelope_near_limit','shadow_review_pending')"
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
    # create_check_constraint re-validates existing rows, so the narrowed CHECK
    # would abort if any row already holds the type being removed. Purge those
    # rows first so the downgrade actually runs (siblings 0023/0033/0036 share
    # this latent hazard; fixed here).
    for table, _ in _TARGETS:
        op.execute(
            f"DELETE FROM {table} WHERE nudge_type = 'shadow_review_pending'"
        )
    for table, constraint in _TARGETS:
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(constraint, table, _OLD)
