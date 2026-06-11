"""B5: notification_events.debt_id — projected debt-cuota notifications

Revision ID: 0027
Revises: 0026
Create Date: 2026-06-10 00:00:00.000000

Debts surface as fixed expenses by projection (no RecurringBill row), so their
cuota notifications can't hang off a bill_occurrence. Add a nullable `debt_id`
target and relax the "exactly one target" CHECK to allow it.

`ON DELETE CASCADE`: a hard-deleted debt removes its notifications (a paid-off /
archived debt simply stops projecting new ones — zero cleanup needed).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_CHECK = (
    "(bill_occurrence_id IS NOT NULL AND custom_event_id IS NULL) "
    "OR (bill_occurrence_id IS NULL AND custom_event_id IS NOT NULL)"
)
_NEW_CHECK = (
    "(bill_occurrence_id IS NOT NULL)::int "
    "+ (custom_event_id IS NOT NULL)::int "
    "+ (debt_id IS NOT NULL)::int = 1"
)


def upgrade() -> None:
    op.add_column(
        "notification_events",
        sa.Column(
            "debt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("debts.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.drop_constraint(
        "ck_notification_events_target", "notification_events", type_="check"
    )
    op.create_check_constraint(
        "ck_notification_events_target", "notification_events", _NEW_CHECK
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_notification_events_target", "notification_events", type_="check"
    )
    op.create_check_constraint(
        "ck_notification_events_target", "notification_events", _OLD_CHECK
    )
    op.drop_column("notification_events", "debt_id")
