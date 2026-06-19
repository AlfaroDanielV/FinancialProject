"""phase 7a: nested envelopes (sub-sobres)

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-08 00:00:00.000000

Lets a spending-cap envelope hold sub-envelopes (a "Viaje" envelope split into
Comida / Hoteles / Tours, with the trip total distributed across them). Adds an
adjacency-list parent link + a depth column capped at 5 levels.

- `parent_id` is ON DELETE CASCADE: hard-deleting a parent removes its whole
  subtree. Each removed node's transactions unlink via the existing
  `transactions.envelope_id … ON DELETE SET NULL` (added in 0022) — they are
  never deleted.
- `depth` is 1-based (root = 1); the CHECK enforces the 5-level cap at the DB.
  A node is only creatable when its parent's depth <= 4 (enforced in the service
  too, with a friendlier error).

No data change for existing rows: they are all roots (parent_id NULL, depth 1).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "envelopes",
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("envelopes.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "envelopes",
        sa.Column(
            "depth",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.create_check_constraint(
        "ck_envelopes_depth", "envelopes", "depth BETWEEN 1 AND 5"
    )
    # Children lookups (subtree roll-up) filter on parent_id; most envelopes are
    # roots, so a partial index keeps it small.
    op.create_index(
        "ix_envelopes_parent",
        "envelopes",
        ["parent_id"],
        postgresql_where=sa.text("parent_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_envelopes_parent", table_name="envelopes")
    op.drop_constraint("ck_envelopes_depth", "envelopes", type_="check")
    op.drop_column("envelopes", "depth")
    op.drop_column("envelopes", "parent_id")
