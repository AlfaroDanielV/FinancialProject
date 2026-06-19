"""account_anchors.source: add 'statement'

Revision ID: 0037
Revises: 0036
Create Date: 2026-06-19 00:00:00.000000

Bank-statement reconciliation appends a `source="statement"` anchor per
deposit/credit account at the fecha de corte (reusing the balance-anchor engine
from 0035). The `ck_account_anchors_source` CHECK enumerates the allowed
provenance values (onboarding | reanchor | migrated); this widens it to include
'statement'. No data change.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0037"
down_revision: Union[str, None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CONSTRAINT = "ck_account_anchors_source"
_TABLE = "account_anchors"
_OLD = "source IN ('onboarding','reanchor','migrated')"
_NEW = "source IN ('onboarding','reanchor','migrated','statement')"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _NEW)


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _OLD)
