"""phase 8 b5 — earned-celebration nudge types

Revision ID: 0042
Revises: 0041
Create Date: 2026-06-26 00:00:00.000000

Phase 8 B5 adds the earned-celebration layer: real positive peaks at genuine
milestones (goal achieved, debt paid off, first full month tracked, stayed
under a sobre's limit for a closed month). These are delivered via the same
Phase 5d nudge rails (Telegram push + the in-app Alertas feed). Both
user_nudges.nudge_type and user_nudge_silences.nudge_type carry a CHECK
enumerating the allowed types (Phase 5d, migration 0008; widened in 0023,
0033, 0036, 0041). This migration adds the four celebration types to both.
No data change.

Sits on top of 0041 (shadow_review_pending).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0042"
down_revision: Union[str, None] = "0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD = (
    "nudge_type IN ('missing_income','stale_pending_confirmation',"
    "'upcoming_bill','over_commitment','duplicate_transaction',"
    "'envelope_near_limit','shadow_review_pending')"
)
_NEW = (
    "nudge_type IN ('missing_income','stale_pending_confirmation',"
    "'upcoming_bill','over_commitment','duplicate_transaction',"
    "'envelope_near_limit','shadow_review_pending',"
    "'goal_achieved','debt_paid_off','first_full_month','under_budget_month')"
)

_TARGETS = (
    ("user_nudges", "ck_user_nudges_nudge_type"),
    ("user_nudge_silences", "ck_user_nudge_silences_nudge_type"),
)

_NEW_TYPES = (
    "goal_achieved",
    "debt_paid_off",
    "first_full_month",
    "under_budget_month",
)


def upgrade() -> None:
    for table, constraint in _TARGETS:
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(constraint, table, _NEW)


def downgrade() -> None:
    # create_check_constraint re-validates existing rows, so the narrowed CHECK
    # would abort if any row already holds a type being removed. Purge those
    # rows first so the downgrade actually runs (the 0041/0033 safeguard).
    type_list = ",".join(f"'{t}'" for t in _NEW_TYPES)
    for table, _ in _TARGETS:
        op.execute(f"DELETE FROM {table} WHERE nudge_type IN ({type_list})")
    for table, constraint in _TARGETS:
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(constraint, table, _OLD)
