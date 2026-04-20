"""update budgets, goals, and weekly_reports for phase 3

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-15 00:00:01.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Budgets: rename monthly_limit → amount_limit, add period + start_date ──
    op.alter_column("budgets", "monthly_limit", new_column_name="amount_limit")
    op.add_column(
        "budgets",
        sa.Column("period", sa.String(20), nullable=False, server_default="monthly"),
    )
    op.add_column(
        "budgets",
        sa.Column("start_date", sa.Date(), nullable=True),
    )

    # ── Goals: rename target_date → deadline, add priority + status ──
    op.alter_column("goals", "target_date", new_column_name="deadline")
    op.add_column(
        "goals",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column(
        "goals",
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
    )
    # Remove is_active since we now use status
    op.drop_column("goals", "is_active")

    # ── Weekly Reports: rename report_date → week_start, add week_end + sent_at ──
    op.alter_column("weekly_reports", "report_date", new_column_name="week_start")
    op.add_column(
        "weekly_reports",
        sa.Column("week_end", sa.Date(), nullable=True),
    )
    op.add_column(
        "weekly_reports",
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    # Backfill week_end = week_start + 6 days for existing rows
    op.execute("UPDATE weekly_reports SET week_end = week_start + INTERVAL '6 days' WHERE week_end IS NULL")
    op.alter_column("weekly_reports", "week_end", nullable=False)


def downgrade() -> None:
    # ── Weekly Reports ──
    op.alter_column("weekly_reports", "week_end", nullable=True)
    op.drop_column("weekly_reports", "sent_at")
    op.drop_column("weekly_reports", "week_end")
    op.alter_column("weekly_reports", "week_start", new_column_name="report_date")

    # ── Goals ──
    op.add_column(
        "goals",
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
    )
    op.execute("UPDATE goals SET is_active = (status = 'active' OR status = 'paused')")
    op.drop_column("goals", "status")
    op.drop_column("goals", "priority")
    op.alter_column("goals", "deadline", new_column_name="target_date")

    # ── Budgets ──
    op.drop_column("budgets", "start_date")
    op.drop_column("budgets", "period")
    op.alter_column("budgets", "amount_limit", new_column_name="monthly_limit")
