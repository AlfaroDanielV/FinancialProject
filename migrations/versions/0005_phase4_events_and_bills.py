"""phase 4: recurring bills, custom events, and notifications

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-16 00:00:00.000000

Tables created:
    - recurring_bills
    - bill_occurrences
    - custom_events
    - notification_rules
    - notification_events

Tables dropped:
    - events (Phase 1 stub, superseded by custom_events)

Seeds:
    - one notification_rules row with scope='global_default' and
      advance_days=[7, 3, 1, 0]
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── drop legacy events table (Phase 1 stub, never wired to any route) ────
    op.drop_table("events")

    # ── recurring_bills ───────────────────────────────────────────────────────
    op.create_table(
        "recurring_bills",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(255), nullable=True),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("amount_expected", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CRC"),
        sa.Column(
            "is_variable_amount",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("frequency", sa.String(20), nullable=False),
        sa.Column("day_of_month", sa.Integer(), nullable=True),
        sa.Column("recurrence_rule", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column(
            "lead_time_days", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        # Optional link to an installment loan in the debts table.
        sa.Column("linked_loan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(
            ["linked_loan_id"], ["debts.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "day_of_month IS NULL OR (day_of_month BETWEEN 1 AND 31)",
            name="ck_recurring_bills_day_of_month",
        ),
    )
    op.create_index(
        "ix_recurring_bills_active_category",
        "recurring_bills",
        ["is_active", "category"],
    )
    op.create_index(
        "ix_recurring_bills_account_id", "recurring_bills", ["account_id"]
    )

    # ── bill_occurrences ──────────────────────────────────────────────────────
    op.create_table(
        "bill_occurrences",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "recurring_bill_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("amount_expected", sa.Numeric(14, 2), nullable=True),
        sa.Column("amount_paid", sa.Numeric(14, 2), nullable=True),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="pending"
        ),
        sa.Column("paid_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["recurring_bill_id"],
            ["recurring_bills.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recurring_bill_id",
            "due_date",
            name="uq_bill_occurrences_bill_due",
        ),
    )
    op.create_index(
        "ix_bill_occurrences_status_due",
        "bill_occurrences",
        ["status", "due_date"],
    )
    op.create_index(
        "ix_bill_occurrences_due_date", "bill_occurrences", ["due_date"]
    )

    # ── custom_events ─────────────────────────────────────────────────────────
    op.create_table(
        "custom_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column(
            "is_all_day",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("event_time", sa.Time(), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CRC"),
        sa.Column("recurrence_rule", sa.Text(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_custom_events_event_date", "custom_events", ["event_date"]
    )

    # ── notification_rules ────────────────────────────────────────────────────
    op.create_table(
        "notification_rules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(30), nullable=False),
        sa.Column(
            "recurring_bill_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "custom_event_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("category", sa.String(50), nullable=True),
        sa.Column("advance_days", postgresql.JSONB(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["recurring_bill_id"],
            ["recurring_bills.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["custom_event_id"],
            ["custom_events.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            """
            (scope = 'bill'
                AND recurring_bill_id IS NOT NULL
                AND custom_event_id IS NULL
                AND category IS NULL)
            OR (scope = 'event'
                AND recurring_bill_id IS NULL
                AND custom_event_id IS NOT NULL
                AND category IS NULL)
            OR (scope = 'category_default'
                AND recurring_bill_id IS NULL
                AND custom_event_id IS NULL
                AND category IS NOT NULL)
            OR (scope = 'global_default'
                AND recurring_bill_id IS NULL
                AND custom_event_id IS NULL
                AND category IS NULL)
            """,
            name="ck_notification_rules_scope_target",
        ),
    )

    # ── notification_events ───────────────────────────────────────────────────
    op.create_table(
        "notification_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "bill_occurrence_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "custom_event_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("trigger_date", sa.Date(), nullable=False),
        sa.Column("advance_days", sa.Integer(), nullable=False),
        sa.Column(
            "channel", sa.String(20), nullable=False, server_default="in_app"
        ),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="pending"
        ),
        sa.Column("delivered_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "acknowledged_at", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column("payload_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["bill_occurrence_id"],
            ["bill_occurrences.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["custom_event_id"],
            ["custom_events.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            """
            (bill_occurrence_id IS NOT NULL AND custom_event_id IS NULL)
            OR (bill_occurrence_id IS NULL AND custom_event_id IS NOT NULL)
            """,
            name="ck_notification_events_target",
        ),
    )
    op.create_index(
        "ix_notification_events_status_trigger",
        "notification_events",
        ["status", "trigger_date"],
    )
    op.create_index(
        "ix_notification_events_channel_status",
        "notification_events",
        ["channel", "status"],
    )

    # ── seed: global default notification rule ───────────────────────────────
    op.execute(
        """
        INSERT INTO notification_rules (scope, advance_days, is_active)
        VALUES ('global_default', '[7, 3, 1, 0]'::jsonb, true)
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_events_channel_status", table_name="notification_events"
    )
    op.drop_index(
        "ix_notification_events_status_trigger", table_name="notification_events"
    )
    op.drop_table("notification_events")

    op.drop_table("notification_rules")

    op.drop_index("ix_custom_events_event_date", table_name="custom_events")
    op.drop_table("custom_events")

    op.drop_index("ix_bill_occurrences_due_date", table_name="bill_occurrences")
    op.drop_index(
        "ix_bill_occurrences_status_due", table_name="bill_occurrences"
    )
    op.drop_table("bill_occurrences")

    op.drop_index(
        "ix_recurring_bills_account_id", table_name="recurring_bills"
    )
    op.drop_index(
        "ix_recurring_bills_active_category", table_name="recurring_bills"
    )
    op.drop_table("recurring_bills")

    # ── recreate legacy events table (matches 0001 schema) ────────────────────
    op.create_table(
        "events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column(
            "is_recurring", sa.Boolean(), server_default=sa.text("false")
        ),
        sa.Column("recurrence_rule", sa.Text(), nullable=True),
        sa.Column(
            "alert_days_before",
            postgresql.ARRAY(sa.Integer()),
            server_default="{30,14,7}",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
