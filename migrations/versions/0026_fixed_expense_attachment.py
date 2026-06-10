"""fixed-expense attachment: recurring_bills + debts envelope_id

Revision ID: 0026
Revises: 0025
Create Date: 2026-06-09 00:00:00.000000

A recurring bill or debt can be attached to an envelope (its expected amount is
then reserved inside the envelope, and the under_coverage gate becomes
per-item). The link is a nullable FK on the OBLIGATION (one obligation → ≤1
envelope; one envelope → many obligations; no join table).

`ON DELETE SET NULL`: hard-deleting an envelope detaches the obligation, never
deletes it. Soft-archive detaches explicitly in `archive_subtree` (the FK only
fires on a real DELETE).

No data change for existing rows: all obligations start unattached
(envelope_id NULL).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("recurring_bills", "debts")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "envelope_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("envelopes.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        # Most obligations are unattached; a partial index keeps the
        # "which obligations live in this envelope" lookup small.
        op.create_index(
            f"ix_{table}_envelope",
            table,
            ["envelope_id"],
            postgresql_where=sa.text("envelope_id IS NOT NULL"),
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_index(f"ix_{table}_envelope", table_name=table)
        op.drop_column(table, "envelope_id")
