"""phase 6c b4: insight extractor cost tracking

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-07 00:00:00.000000

B4 runs the user-memory extractor asynchronously after selected bot
turns. Each extractor run is logged in llm_query_dispatches using the
same token columns as query dispatches. `extractor_run_id` lets an
originating query-dispatch row point at the follow-up extractor run
that was launched from it.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "llm_query_dispatches",
        sa.Column(
            "extractor_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "llm_query_dispatches",
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=True),
    )
    op.create_foreign_key(
        "fk_llm_query_dispatches_extractor_run",
        "llm_query_dispatches",
        "llm_query_dispatches",
        ["extractor_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_llm_query_dispatches_extractor_run_id",
        "llm_query_dispatches",
        ["extractor_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_llm_query_dispatches_extractor_run_id",
        table_name="llm_query_dispatches",
    )
    op.drop_constraint(
        "fk_llm_query_dispatches_extractor_run",
        "llm_query_dispatches",
        type_="foreignkey",
    )
    op.drop_column("llm_query_dispatches", "estimated_cost_usd")
    op.drop_column("llm_query_dispatches", "extractor_run_id")
