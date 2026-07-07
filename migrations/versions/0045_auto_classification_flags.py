"""transactions: auto-classification flags (category + envelope)

Revision ID: 0045
Revises: 0044
Create Date: 2026-07-06 12:00:00.000000

Auto-classification at capture (operator ask 2026-07-06): every capture path
best-effort fills `category`/`category_id` and, for expenses, `envelope_id`
from the user's OWN confirmed history (deterministic — never an LLM decision,
never a synonym map). These two flags mark values the system filled so the
UI can render a visible "auto" tag and a user correction clears them; they
also fence history candidates (only user-set rows feed future suggestions,
so a wrong auto-assign can never self-reinforce). Server defaults false —
existing rows are all user-set. See vault `Decision - Auto-Classification At
Capture (History + Hint)`.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: Union[str, None] = "0044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column(
            "category_auto_assigned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "transactions",
        sa.Column(
            "envelope_auto_assigned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("transactions", "envelope_auto_assigned")
    op.drop_column("transactions", "category_auto_assigned")
