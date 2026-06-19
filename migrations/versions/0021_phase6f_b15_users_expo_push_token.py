"""phase 6f b15: users.expo_push_token (schema-only prep for P8 APNs)

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-30 00:00:00.000000

Adds users.expo_push_token as schema-only preparation for native push
delivery. Phase 6f does NOT deliver to it — nudges + shadow approvals stay
Telegram-only (decision 3.7). The column is nullable (a user may never
register a device, or may run the app via the bearer-token path without a
push token). No uniqueness constraint: re-registering the app on the same
device rotates the token in place, and cross-user collisions are not a
correctness concern for the personal MVP. APNs delivery + a real Expo push
token writer land at P8 once the Apple Developer Program is enrolled.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("expo_push_token", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "expo_push_token")
