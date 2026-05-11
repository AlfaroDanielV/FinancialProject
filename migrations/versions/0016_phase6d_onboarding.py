"""phase 6d: onboarding & self-registration foundation

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-08 00:00:00.000000

What this does:
    1. Creates three new tables for the Phase 6d onboarding pipeline:
        - recurring_incomes — modela explícitamente los ciclos CR-specific
          (salary, aguinaldo, salario_escolar) como first-class citizens.
          aguinaldo y salario_escolar requieren base_salary_link_id (CHECK).
        - magic_link_tokens — single-use, TTL 30 min, sin refresh tokens
          (decisions doc §3.3 + resolución 9.1). jti UNIQUE para
          invalidación; token_hash bcrypt como defense in depth.
        - lazy_detection_events — telemetría per-evento de la lógica de
          lazy detection en el write dispatcher (B8). Permite iterar el
          threshold 0.85 post-launch sin perder datos.
    2. Extiende `accounts` con dos columnas que el SPA form de B5 necesita:
        - currency: ya estaba implícito a nivel de user; ahora es explícito
          per-cuenta (multi-cuenta CRC + USD es common case CR).
        - initial_balance: anchor para reportes y reconciliación; balance
          actual sigue siendo derivado de transactions.

Lo que NO se toca (decisión registrada en docs/phase-6d-decisions.md §9):
    - debts: ya tiene term_months, payment_due_day, start_date, interest_rate
      fractional. La validación NOT NULL para nuevas deudas se aplica en
      Pydantic schemas (B2), no en el DB.
    - recurring_bills: se reusa tal cual desde Phase 4 (resolución 9.2).
    - categorías default: viven en api/data/categories_cr.py, no en DB
      (transactions.category sigue siendo free-form text).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── recurring_incomes ───────────────────────────────────────────────────
    # CR cycles as first-class enum: aguinaldo (Dec, ~13th salary) and
    # salario_escolar (Jan, accumulated 8.33%/mo) are not "tags on monthly"
    # — they have different payment dates, different computation logic,
    # and the user thinks of them as distinct. Modeling them as enum
    # values lets the affordability engine (P7) reason about them
    # without string parsing.
    #
    # base_salary_link_id is self-FK: aguinaldo / salario_escolar derive
    # their amount from a salary entry. CHECK enforces that link is
    # present for those two types. ON DELETE CASCADE: if the base salary
    # is deleted (e.g. user changed jobs), the derived aguinaldo /
    # salario_escolar rows go with it — they're tied to a specific
    # employment relationship in real-life CR semantics. SET NULL would
    # leave them orphaned and violate the CHECK constraint.
    op.create_table(
        "recurring_incomes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("income_type", sa.String(32), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=True),
        sa.Column(
            "currency", sa.String(3), nullable=False, server_default="CRC"
        ),
        sa.Column("frequency", sa.String(16), nullable=False),
        sa.Column("next_payment_date", sa.Date(), nullable=False),
        sa.Column(
            "base_salary_link_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
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
            "income_type IN ('salary','aguinaldo','salario_escolar',"
            "'freelance','other')",
            name="ck_recurring_incomes_type",
        ),
        sa.CheckConstraint(
            "frequency IN ('weekly','biweekly','monthly','annual')",
            name="ck_recurring_incomes_frequency",
        ),
        sa.CheckConstraint(
            "income_type NOT IN ('aguinaldo','salario_escolar') "
            "OR base_salary_link_id IS NOT NULL",
            name="ck_recurring_incomes_cr_cycles_need_base",
        ),
    )
    op.create_foreign_key(
        "fk_recurring_incomes_base_salary",
        source_table="recurring_incomes",
        referent_table="recurring_incomes",
        local_cols=["base_salary_link_id"],
        remote_cols=["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_recurring_incomes_user_active",
        "recurring_incomes",
        ["user_id", "is_active"],
    )

    # ── magic_link_tokens ────────────────────────────────────────────────────
    # Single-use, TTL 30 min. Token is raw `<selector>.<verifier>` (Resolución
    # 9.9): selector is plain + indexed UNIQUE for O(1) lookup; verifier is
    # bcrypt'd in token_hash. jti is the JWT identifier issued in the session
    # cookie post-exchange (Resolución 9.1) — useful for future revocation.
    op.create_table(
        "magic_link_tokens",
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
            "selector",
            sa.String(32),
            nullable=False,
            unique=True,
        ),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column(
            "jti",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            unique=True,
        ),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "purpose IN ('onboarding','edit_session')",
            name="ck_magic_link_tokens_purpose",
        ),
    )
    op.create_index(
        "ix_magic_link_tokens_user_used",
        "magic_link_tokens",
        ["user_id", "used_at"],
    )

    # ── lazy_detection_events ──────────────────────────────────────────────
    # Telemetría del threshold 0.85 + decisión del usuario. Sin esto no
    # podemos iterar el threshold post-launch con evidencia real.
    op.create_table(
        "lazy_detection_events",
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
        sa.Column("hint_type", sa.String(16), nullable=False),
        sa.Column("hint_text", sa.Text(), nullable=False),
        sa.Column(
            "fuzzy_match_score", sa.Numeric(3, 2), nullable=True
        ),
        sa.Column(
            "resolution",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "matched_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "resolved_at", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.CheckConstraint(
            "hint_type IN ('account','bank','debt')",
            name="ck_lazy_detection_events_hint_type",
        ),
        sa.CheckConstraint(
            "resolution IN ('created','linked_existing','dismissed','pending')",
            name="ck_lazy_detection_events_resolution",
        ),
        sa.CheckConstraint(
            "fuzzy_match_score IS NULL "
            "OR (fuzzy_match_score >= 0 AND fuzzy_match_score <= 1)",
            name="ck_lazy_detection_events_score_range",
        ),
    )
    op.create_index(
        "ix_lazy_detection_events_user_created",
        "lazy_detection_events",
        ["user_id", sa.text("created_at DESC")],
    )

    # ── accounts: extend with currency + initial_balance ───────────────────
    # Existing rows backfill safely: CRC + 0. The defaults are kept on the
    # column so future inserts that omit these stay valid (the API will
    # always provide them, but the DB safety net stays).
    op.add_column(
        "accounts",
        sa.Column(
            "currency",
            sa.String(3),
            nullable=False,
            server_default="CRC",
        ),
    )
    op.add_column(
        "accounts",
        sa.Column(
            "initial_balance",
            sa.Numeric(14, 2),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("accounts", "initial_balance")
    op.drop_column("accounts", "currency")

    op.drop_index(
        "ix_lazy_detection_events_user_created",
        table_name="lazy_detection_events",
    )
    op.drop_table("lazy_detection_events")

    op.drop_index(
        "ix_magic_link_tokens_user_used",
        table_name="magic_link_tokens",
    )
    op.drop_table("magic_link_tokens")

    op.drop_index(
        "ix_recurring_incomes_user_active",
        table_name="recurring_incomes",
    )
    op.drop_constraint(
        "fk_recurring_incomes_base_salary",
        "recurring_incomes",
        type_="foreignkey",
    )
    op.drop_table("recurring_incomes")
