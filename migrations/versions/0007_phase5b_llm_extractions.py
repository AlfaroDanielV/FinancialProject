"""phase 5b: llm_extractions table

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-20 00:00:00.000000

Adds the `llm_extractions` table used by the Telegram extractor to log every
call for later evaluation (not analytics). Each row records the LLM input
hash, the parsed ExtractionResult, latency, tokens, and the model used — so
when a user reports "the bot misunderstood me", we can find the exact call
without replaying against a live model.

Schema notes:
- `user_id` FK → users.id, ON DELETE RESTRICT (consistent with 0006 scoping).
- `message_hash` is a hex SHA-256 of the raw Telegram text. Stored instead
  of the raw content so logs can live at INFO without leaking personal
  finance data; the dispatcher's payload_snapshot is where the full parsed
  data lives.
- `extraction` is JSONB so we can evolve the ExtractionResult schema
  without migrations.
- `(user_id, created_at DESC)` index covers the per-user history queries.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_extractions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("message_hash", sa.String(64), nullable=False),
        sa.Column("intent", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column(
            "extraction",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_creation_tokens", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_llm_extractions_user_created",
        "llm_extractions",
        ["user_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_llm_extractions_user_created", table_name="llm_extractions"
    )
    op.drop_table("llm_extractions")
