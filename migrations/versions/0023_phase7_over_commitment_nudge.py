"""phase 7: over_commitment nudge type

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-08 00:00:00.000000

Phase 7 surface 3 adds a proactive over-commitment nudge. Both
user_nudges.nudge_type and user_nudge_silences.nudge_type carry a CHECK that
enumerates the allowed types (Phase 5d, migration 0008). This migration widens
both to include 'over_commitment'. No data change — the column type/size is
unchanged.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD = "nudge_type IN ('missing_income','stale_pending_confirmation','upcoming_bill')"
_NEW = (
    "nudge_type IN ('missing_income','stale_pending_confirmation',"
    "'upcoming_bill','over_commitment')"
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
