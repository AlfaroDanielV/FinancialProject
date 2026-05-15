"""phase 6e b8: recurring_incomes soft-delete column

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-15 00:00:00.000000

Adds recurring_incomes.archived for the B8 pause/archive split:
paused = is_active=false + archived=false (visible in list with badge);
archived = is_active=false + archived=true (default-hidden, opt-in show).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "recurring_incomes",
        sa.Column(
            "archived",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("recurring_incomes", "archived")
