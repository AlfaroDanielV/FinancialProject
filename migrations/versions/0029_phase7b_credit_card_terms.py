"""phase 7b: credit_card_terms — card parameters 1:1 on the credit account

Revision ID: 0029
Revises: 0028
Create Date: 2026-06-11 00:00:00.000000

Card terms are ACCOUNT PARAMETERS, not a Debt: the card's balance is already
computed live from transactions (`compute_account_balances`); a
`Debt(debt_type='credit_card')` row would be a second, static source of truth
and its French-amortization endpoints are wrong for revolving credit. This
table stores parameters only — there is deliberately NO balance column. See
vault `Decision - Credit Card Terms As Account Parameters (Not A Debt)`.

- `account_id` UNIQUE → 1:1 with the credit account; ON DELETE CASCADE (the
  Phase 7b hard delete removes the terms with the account).
- Rates are 0–1 fractions (18% → 0.18), like `debts.interest_rate`.
- CR cards: mínimo = max(minimum_payment_pct × saldo, minimum_payment_floor).
- `envelope_id` ON DELETE SET NULL — the B5 obligation attachment, mirroring
  recurring_bills/debts (migration 0026).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "credit_card_terms",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        # Annual purchase rate as a 0–1 fraction (CR cards run ~0.30–0.50).
        sa.Column("annual_interest_rate", sa.Numeric(5, 4), nullable=False),
        sa.Column("cash_advance_rate", sa.Numeric(5, 4), nullable=True),
        # mínimo = max(pct × saldo, piso)
        sa.Column("minimum_payment_pct", sa.Numeric(5, 4), nullable=False),
        sa.Column("minimum_payment_floor", sa.Numeric(12, 2), nullable=True),
        sa.Column("credit_limit", sa.Numeric(14, 2), nullable=True),
        # Día de corte (statement) / fecha límite de pago.
        sa.Column("statement_day", sa.Integer(), nullable=True),
        sa.Column("payment_due_day", sa.Integer(), nullable=False),
        sa.Column(
            "envelope_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("envelopes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "annual_interest_rate >= 0 AND annual_interest_rate <= 1",
            name="ck_card_terms_rate_fraction",
        ),
        sa.CheckConstraint(
            "minimum_payment_pct > 0 AND minimum_payment_pct <= 1",
            name="ck_card_terms_min_pct_fraction",
        ),
        sa.CheckConstraint(
            "statement_day IS NULL OR (statement_day BETWEEN 1 AND 31)",
            name="ck_card_terms_statement_day",
        ),
        sa.CheckConstraint(
            "payment_due_day BETWEEN 1 AND 31",
            name="ck_card_terms_due_day",
        ),
    )
    op.create_index(
        "ix_credit_card_terms_user", "credit_card_terms", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_credit_card_terms_user", table_name="credit_card_terms")
    op.drop_table("credit_card_terms")
