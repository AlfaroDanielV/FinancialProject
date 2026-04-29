"""phase 6a: cache token metrics on llm_query_dispatches

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-28 00:00:00.000000

Adds two nullable Integer columns capturing Anthropic's prompt cache
counters (`cache_read_input_tokens`, `cache_creation_input_tokens`).
Both are nullable because rows logged before this migration ran were
written without them. Forward-fills are not worth the migration risk.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "llm_query_dispatches",
        sa.Column("cache_read_input_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "llm_query_dispatches",
        sa.Column("cache_creation_input_tokens", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("llm_query_dispatches", "cache_creation_input_tokens")
    op.drop_column("llm_query_dispatches", "cache_read_input_tokens")
