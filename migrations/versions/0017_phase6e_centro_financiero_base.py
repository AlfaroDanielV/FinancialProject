"""phase 6e: centro financiero base schema

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-12 00:00:00.000000

Phase 6e expands the onboarding SPA into the read-heavy Centro Financiero.
This migration keeps the existing pre-6e `goals` table and upgrades it in
place, then adds the new base entities needed by the full SPA.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEFAULT_CATEGORIES = (
    ("alimentación", "expense", "#2f855a", "shopping-cart"),
    ("transporte", "expense", "#2b6cb0", "car"),
    ("servicios", "expense", "#805ad5", "plug"),
    ("salud", "expense", "#c53030", "heart-pulse"),
    ("ocio", "expense", "#dd6b20", "ticket"),
    ("vivienda", "expense", "#4a5568", "home"),
    ("ingreso_salario", "income", "#047857", "briefcase"),
    ("ingreso_extra", "income", "#0891b2", "wallet"),
    ("deudas", "expense", "#b7791f", "receipt"),
    ("otros", "both", "#718096", "circle-ellipsis"),
)


def _default_category_values_sql() -> str:
    rows = ",\n        ".join(
        f"('{name}', '{kind}', '{color}', '{icon}')"
        for name, kind, color, icon in DEFAULT_CATEGORIES
    )
    return rows


def upgrade() -> None:
    # User display currency is read-time presentation state. Existing users
    # inherit their base currency once; later user preference changes should
    # only update display_currency.
    op.add_column(
        "users",
        sa.Column("display_currency", sa.String(3), nullable=True),
    )
    op.execute(
        "UPDATE users SET display_currency = LEFT(COALESCE(currency, 'CRC'), 3) "
        "WHERE display_currency IS NULL"
    )
    op.alter_column(
        "users",
        "display_currency",
        nullable=False,
        server_default="CRC",
    )

    # 6e uses archived semantics in the SPA while preserving is_active for
    # older handlers that already read it.
    op.add_column(
        "accounts",
        sa.Column(
            "archived",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.execute(
        "UPDATE accounts SET archived = NOT COALESCE(is_active, true)"
    )

    # Upgrade the existing goals table in place. Migration 0003 renamed the
    # original target_date column to deadline; 6e restores target_date as the
    # public contract.
    op.alter_column("goals", "deadline", new_column_name="target_date")
    op.add_column(
        "goals",
        sa.Column(
            "target_currency",
            sa.String(3),
            nullable=False,
            server_default="CRC",
        ),
    )
    op.add_column(
        "goals",
        sa.Column(
            "linked_account_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_goals_linked_account_id_accounts",
        "goals",
        "accounts",
        ["linked_account_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute("UPDATE goals SET status = 'achieved' WHERE status = 'completed'")
    op.create_check_constraint(
        "ck_goals_status_6e",
        "goals",
        "status IN ('active','achieved','abandoned','paused')",
    )

    op.create_table(
        "goal_contributions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("amount > 0", name="ck_goal_contributions_amount"),
    )
    op.create_index(
        "ix_goal_contributions_goal_occurred",
        "goal_contributions",
        ["goal_id", sa.text("occurred_at DESC")],
    )

    op.create_table(
        "transfers",
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
            "from_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "to_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("fx_rate", sa.Numeric(18, 8), nullable=True),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("amount > 0", name="ck_transfers_amount"),
        sa.CheckConstraint(
            "from_account_id <> to_account_id",
            name="ck_transfers_distinct_accounts",
        ),
        sa.CheckConstraint(
            "fx_rate IS NULL OR fx_rate > 0", name="ck_transfers_fx_rate"
        ),
    )
    op.create_index(
        "ix_transfers_user_occurred",
        "transfers",
        ["user_id", sa.text("occurred_at DESC")],
    )

    op.create_table(
        "user_categories",
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
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("color", sa.String(7), nullable=False),
        sa.Column("icon", sa.String(64), nullable=True),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "archived",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
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
            "kind IN ('income','expense','both')",
            name="ck_user_categories_kind",
        ),
        sa.CheckConstraint(
            "color ~ '^#[0-9A-Fa-f]{6}$'",
            name="ck_user_categories_color_hex",
        ),
    )
    op.create_index(
        "ix_user_categories_user_kind_archived",
        "user_categories",
        ["user_id", "kind", "archived"],
    )
    op.create_index(
        "uq_user_categories_active_name",
        "user_categories",
        ["user_id", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("archived = false"),
    )

    op.execute(
        f"""
        INSERT INTO user_categories
            (id, user_id, name, kind, color, icon, is_default, archived)
        SELECT
            gen_random_uuid(), u.id, c.name, c.kind, c.color, c.icon, true, false
        FROM users u
        CROSS JOIN (
            VALUES
            {_default_category_values_sql()}
        ) AS c(name, kind, color, icon)
        ON CONFLICT DO NOTHING
        """
    )

    op.add_column(
        "transactions",
        sa.Column(
            "transfer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transfers.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "transactions",
        sa.Column(
            "category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_categories.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_transactions_transfer_id", "transactions", ["transfer_id"]
    )
    op.create_index(
        "ix_transactions_category_id", "transactions", ["category_id"]
    )

    op.create_table(
        "currency_rates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("quote_currency", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(18, 8), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("rate > 0", name="ck_currency_rates_rate"),
        sa.UniqueConstraint(
            "base_currency",
            "quote_currency",
            "as_of",
            name="uq_currency_rates_pair_date",
        ),
    )
    op.create_index(
        "ix_currency_rates_pair_as_of",
        "currency_rates",
        ["base_currency", "quote_currency", sa.text("as_of DESC")],
    )

    op.add_column(
        "magic_link_tokens",
        sa.Column("target_path", sa.String(500), nullable=True),
    )

    op.execute(
        """
        CREATE MATERIALIZED VIEW mv_monthly_summary_by_user AS
        WITH converted AS (
            SELECT
                t.user_id,
                date_trunc('month', t.transaction_date)::date AS month,
                u.display_currency,
                t.transfer_id IS NOT NULL AS is_transfer,
                (
                    t.amount
                    * CASE
                        WHEN t.currency = u.display_currency THEN 1
                        ELSE COALESCE(
                            (
                                SELECT cr.rate
                                FROM currency_rates cr
                                WHERE cr.base_currency = t.currency
                                  AND cr.quote_currency = u.display_currency
                                  AND cr.as_of <= t.transaction_date
                                ORDER BY cr.as_of DESC
                                LIMIT 1
                            ),
                            1
                        )
                    END
                )::numeric(18, 2) AS converted_amount
            FROM transactions t
            JOIN users u ON u.id = t.user_id
            WHERE COALESCE(t.status, 'confirmed') = 'confirmed'
        )
        SELECT
            user_id,
            month,
            display_currency,
            COALESCE(
                SUM(CASE WHEN NOT is_transfer AND converted_amount > 0
                         THEN converted_amount ELSE 0 END),
                0
            )::numeric(18, 2) AS income_total,
            COALESCE(
                SUM(CASE WHEN NOT is_transfer AND converted_amount < 0
                         THEN abs(converted_amount) ELSE 0 END),
                0
            )::numeric(18, 2) AS expense_total,
            COALESCE(
                SUM(CASE WHEN NOT is_transfer THEN converted_amount ELSE 0 END),
                0
            )::numeric(18, 2) AS net_flow,
            (
                COALESCE(
                    SUM(CASE WHEN NOT is_transfer AND converted_amount > 0
                             THEN converted_amount ELSE 0 END),
                    0
                )
                -
                COALESCE(
                    SUM(CASE WHEN NOT is_transfer AND converted_amount < 0
                             THEN abs(converted_amount) ELSE 0 END),
                    0
                )
            )
            / NULLIF(
                COALESCE(
                    SUM(CASE WHEN NOT is_transfer AND converted_amount > 0
                             THEN converted_amount ELSE 0 END),
                    0
                ),
                0
            ) AS savings_rate,
            count(*) FILTER (WHERE NOT is_transfer) AS transaction_count,
            count(*) FILTER (WHERE is_transfer) AS transfer_rows_excluded
        FROM converted
        GROUP BY user_id, month, display_currency
        """
    )
    op.create_index(
        "ix_mv_monthly_summary_by_user_lookup",
        "mv_monthly_summary_by_user",
        ["user_id", "month", "display_currency"],
        unique=True,
    )

    op.execute(
        """
        CREATE MATERIALIZED VIEW mv_yearly_summary_by_user AS
        SELECT
            user_id,
            date_trunc('year', month)::date AS year,
            display_currency,
            SUM(income_total)::numeric(18, 2) AS income_total,
            SUM(expense_total)::numeric(18, 2) AS expense_total,
            SUM(net_flow)::numeric(18, 2) AS net_flow,
            (SUM(income_total) - SUM(expense_total))
                / NULLIF(SUM(income_total), 0) AS savings_rate,
            SUM(transaction_count)::int AS transaction_count,
            SUM(transfer_rows_excluded)::int AS transfer_rows_excluded
        FROM mv_monthly_summary_by_user
        GROUP BY user_id, date_trunc('year', month)::date, display_currency
        """
    )
    op.create_index(
        "ix_mv_yearly_summary_by_user_lookup",
        "mv_yearly_summary_by_user",
        ["user_id", "year", "display_currency"],
        unique=True,
    )


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_yearly_summary_by_user")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_monthly_summary_by_user")

    op.drop_column("magic_link_tokens", "target_path")

    op.drop_index(
        "ix_currency_rates_pair_as_of", table_name="currency_rates"
    )
    op.drop_table("currency_rates")

    op.drop_index("ix_transactions_category_id", table_name="transactions")
    op.drop_index("ix_transactions_transfer_id", table_name="transactions")
    op.drop_column("transactions", "category_id")
    op.drop_column("transactions", "transfer_id")

    op.drop_index(
        "uq_user_categories_active_name", table_name="user_categories"
    )
    op.drop_index(
        "ix_user_categories_user_kind_archived", table_name="user_categories"
    )
    op.drop_table("user_categories")

    op.drop_index("ix_transfers_user_occurred", table_name="transfers")
    op.drop_table("transfers")

    op.drop_index(
        "ix_goal_contributions_goal_occurred",
        table_name="goal_contributions",
    )
    op.drop_table("goal_contributions")

    op.drop_constraint("ck_goals_status_6e", "goals", type_="check")
    op.drop_constraint(
        "fk_goals_linked_account_id_accounts", "goals", type_="foreignkey"
    )
    op.drop_column("goals", "linked_account_id")
    op.drop_column("goals", "target_currency")
    op.alter_column("goals", "target_date", new_column_name="deadline")
    op.execute("UPDATE goals SET status = 'completed' WHERE status = 'achieved'")

    op.drop_column("accounts", "archived")
    op.drop_column("users", "display_currency")
