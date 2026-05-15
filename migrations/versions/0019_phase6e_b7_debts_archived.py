"""phase 6e b7: debts soft-delete column

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-13 00:00:00.000000

Adds debts.archived for the B7 pause/archive split: paused = is_active=false
+ archived=false (visible in list with badge, excluded from overview totals);
archived = is_active=false + archived=true (hidden from default list).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "debts",
        sa.Column(
            "archived",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("debts", "archived")
