"""phase 6c gmail onboarding discovery

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-08 00:00:00.000000

Adds a low-cost Gmail sender discovery audit table and extends whitelist
source values for the two new onboarding modes. ``preset_tap`` remains valid
for historical Phase 6b rows.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gmail_discovery_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("keywords", postgresql.JSONB(), nullable=False),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "senders_found",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "senders_confirmed_as_bank",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_index(
        "ix_gmail_discovery_runs_user_started",
        "gmail_discovery_runs",
        ["user_id", sa.text("started_at DESC")],
    )

    op.alter_column(
        "gmail_sender_whitelist",
        "source",
        existing_type=sa.String(20),
        type_=sa.String(30),
        existing_nullable=False,
    )
    op.drop_constraint(
        "ck_gmail_sender_whitelist_source",
        "gmail_sender_whitelist",
        type_="check",
    )
    op.create_check_constraint(
        "ck_gmail_sender_whitelist_source",
        "gmail_sender_whitelist",
        "source IN ('preset_tap','custom_typed','imported','discovered','manual_with_bank_hint')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_gmail_sender_whitelist_source",
        "gmail_sender_whitelist",
        type_="check",
    )
    op.create_check_constraint(
        "ck_gmail_sender_whitelist_source",
        "gmail_sender_whitelist",
        "source IN ('preset_tap','custom_typed','imported')",
    )
    op.alter_column(
        "gmail_sender_whitelist",
        "source",
        existing_type=sa.String(30),
        type_=sa.String(20),
        existing_nullable=False,
    )
    op.drop_index(
        "ix_gmail_discovery_runs_user_started",
        table_name="gmail_discovery_runs",
    )
    op.drop_table("gmail_discovery_runs")

