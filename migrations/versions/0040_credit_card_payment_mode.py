"""credit_card_terms: add payment_mode (minimum | full / contado)

Revision ID: 0040
Revises: 0039
Create Date: 2026-06-25 12:00:00.000000

A credit card's recurring payment can be projected at either the monthly
minimum (`minimum`) or the full statement balance paid de contado (`full`).
This is a per-card budgeting preference; the amount is still computed LIVE
(no stored balance, no materialized recurring_bills row) — `payment_mode`
only selects which live figure the upcoming-payment projection surfaces.
Existing cards default to 'minimum' (zero behavior change at cutover). See
vault `Decision - Credit Card Contado Recurring Payment`.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: Union[str, None] = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CONSTRAINT = "ck_credit_card_terms_payment_mode"


def upgrade() -> None:
    op.add_column(
        "credit_card_terms",
        sa.Column(
            "payment_mode",
            sa.Text(),
            nullable=False,
            server_default="minimum",
        ),
    )
    op.create_check_constraint(
        _CONSTRAINT,
        "credit_card_terms",
        "payment_mode IN ('minimum','full')",
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "credit_card_terms", type_="check")
    op.drop_column("credit_card_terms", "payment_mode")
