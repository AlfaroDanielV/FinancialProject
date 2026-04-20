"""phase 5a: users multi-tenant foundation

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-20 00:00:00.000000

What this does:
    1. Extends `users` with email, full_name, phone_number, country, locale,
       shortcut_token, telegram_user_id, whatsapp_phone, status, updated_at.
       (`name` is renamed to `full_name`. `currency`, `timezone`, `created_at`
       are kept.)
    2. Reads LEGACY_USER_EMAIL, LEGACY_USER_NAME, LEGACY_SHORTCUT_TOKEN from
       env. Fails loudly if any are missing. Reads optional DEFAULT_USER_ID:
         - if set and a row exists, fills in the new fields on that row.
         - else inserts a fresh legacy user.
    3. Adds `user_id` (NULL) to the Phase 4 tables that lacked it
       (recurring_bills, bill_occurrences, custom_events, notification_rules,
       notification_events), backfills with the legacy user's id, alters to
       NOT NULL, and adds FKs with ON DELETE RESTRICT.
    4. Adds (user_id) and (user_id, hot-column) indexes.
    5. Adds two new per-user UNIQUE indexes:
         - transactions(user_id, source_ref) WHERE source_ref IS NOT NULL
         - accounts(user_id, name)           WHERE is_active
    6. Backfills the seeded global_default notification_rule with user_id.

Pre-existing FKs on already-scoped tables (transactions/accounts/budgets/
goals/weekly_reports/debts → users.id) are left as NO ACTION; we did not
flag a need to convert them.
"""
from __future__ import annotations

import os
import secrets
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── tables that get a NEW user_id column added ────────────────────────────────
_NEW_SCOPED_TABLES: tuple[str, ...] = (
    "recurring_bills",
    "bill_occurrences",
    "custom_events",
    "notification_rules",
    "notification_events",
)


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"Migration 0006 requires env var {name}. Set it (e.g. in .env) "
            f"before running `alembic upgrade head`."
        )
    return val


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. extend users table ────────────────────────────────────────────────
    # Rename name → full_name (preserving data).
    op.alter_column("users", "name", new_column_name="full_name")

    # Add new columns nullable; we'll backfill then tighten.
    op.add_column("users", sa.Column("email", sa.String(320), nullable=True))
    op.add_column(
        "users", sa.Column("phone_number", sa.String(32), nullable=True)
    )
    op.add_column(
        "users",
        sa.Column(
            "country",
            sa.String(2),
            nullable=False,
            server_default="CR",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "locale",
            sa.String(10),
            nullable=False,
            server_default="es-CR",
        ),
    )
    op.add_column(
        "users", sa.Column("shortcut_token", sa.String(128), nullable=True)
    )
    op.add_column(
        "users", sa.Column("telegram_user_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "users", sa.Column("whatsapp_phone", sa.String(32), nullable=True)
    )
    op.add_column(
        "users",
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_check_constraint(
        "ck_users_status", "users", "status IN ('active','suspended')"
    )

    # ── 2. backfill / insert legacy user ─────────────────────────────────────
    legacy_email = _require_env("LEGACY_USER_EMAIL").strip().lower()
    legacy_name = _require_env("LEGACY_USER_NAME").strip()
    legacy_token = _require_env("LEGACY_SHORTCUT_TOKEN").strip()
    default_user_id = (os.environ.get("DEFAULT_USER_ID") or "").strip()

    legacy_user_id: str | None = None
    if default_user_id:
        existing = bind.execute(
            sa.text("SELECT id FROM users WHERE id = :id"),
            {"id": default_user_id},
        ).scalar()
        if existing is not None:
            legacy_user_id = str(existing)
            bind.execute(
                sa.text(
                    """
                    UPDATE users
                       SET email          = :email,
                           full_name      = :name,
                           shortcut_token = :token
                     WHERE id = :id
                    """
                ),
                {
                    "email": legacy_email,
                    "name": legacy_name,
                    "token": legacy_token,
                    "id": default_user_id,
                },
            )

    if legacy_user_id is None:
        # Match existing row by email (idempotent re-runs), else insert.
        existing = bind.execute(
            sa.text("SELECT id FROM users WHERE email = :email"),
            {"email": legacy_email},
        ).scalar()
        if existing is not None:
            legacy_user_id = str(existing)
            bind.execute(
                sa.text(
                    "UPDATE users SET full_name = :name, shortcut_token = :token "
                    "WHERE id = :id"
                ),
                {
                    "name": legacy_name,
                    "token": legacy_token,
                    "id": legacy_user_id,
                },
            )
        else:
            row = bind.execute(
                sa.text(
                    """
                    INSERT INTO users (email, full_name, shortcut_token)
                    VALUES (:email, :name, :token)
                    RETURNING id
                    """
                ),
                {
                    "email": legacy_email,
                    "name": legacy_name,
                    "token": legacy_token,
                },
            ).first()
            legacy_user_id = str(row[0])

    # Fail closed: every row must have a non-null email and shortcut_token.
    null_users = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM users WHERE email IS NULL OR shortcut_token IS NULL"
        )
    ).scalar_one()
    if null_users:
        raise RuntimeError(
            f"{null_users} pre-existing users could not be backfilled. "
            f"Set LEGACY_* env vars and re-run, or hand-fill those rows "
            f"before retrying."
        )

    # Tighten constraints + uniqueness now that everyone has a value.
    op.alter_column("users", "email", nullable=False)
    op.alter_column("users", "shortcut_token", nullable=False)
    op.create_unique_constraint("uq_users_email", "users", ["email"])
    op.create_unique_constraint(
        "uq_users_shortcut_token", "users", ["shortcut_token"]
    )
    op.create_unique_constraint(
        "uq_users_telegram_user_id", "users", ["telegram_user_id"]
    )
    op.create_unique_constraint(
        "uq_users_whatsapp_phone", "users", ["whatsapp_phone"]
    )
    # Drop the server_defaults we used only to backfill.
    op.alter_column("users", "country", server_default=None)
    op.alter_column("users", "locale", server_default=None)
    op.alter_column("users", "status", server_default=None)
    op.alter_column("users", "updated_at", server_default=None)

    # ── 3. add user_id to Phase 4 tables ─────────────────────────────────────
    for table in _NEW_SCOPED_TABLES:
        op.add_column(
            table,
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        bind.execute(
            sa.text(f"UPDATE {table} SET user_id = :uid"),
            {"uid": legacy_user_id},
        )
        op.alter_column(table, "user_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table}_user_id",
            table,
            "users",
            ["user_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])

    # ── 4. composite indexes for hot read paths ──────────────────────────────
    op.create_index(
        "ix_recurring_bills_user_active",
        "recurring_bills",
        ["user_id", "is_active"],
    )
    op.create_index(
        "ix_bill_occurrences_user_due",
        "bill_occurrences",
        ["user_id", "due_date"],
    )
    op.create_index(
        "ix_bill_occurrences_user_status_due",
        "bill_occurrences",
        ["user_id", "status", "due_date"],
    )
    op.create_index(
        "ix_custom_events_user_event_date",
        "custom_events",
        ["user_id", "event_date"],
    )
    op.create_index(
        "ix_notification_rules_user_scope",
        "notification_rules",
        ["user_id", "scope"],
    )
    op.create_index(
        "ix_notification_events_user_status_trigger",
        "notification_events",
        ["user_id", "status", "trigger_date"],
    )

    # ── 5. per-user uniqueness on existing tables ────────────────────────────
    # transactions: email-parsed dedup fingerprint, scoped per user.
    op.create_index(
        "uq_transactions_user_source_ref",
        "transactions",
        ["user_id", "source_ref"],
        unique=True,
        postgresql_where=sa.text("source_ref IS NOT NULL"),
    )
    # accounts: a user can't have two active accounts with the same name.
    op.create_index(
        "uq_accounts_user_active_name",
        "accounts",
        ["user_id", "name"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    # ── 5. drop per-user uniqueness on existing tables ──
    op.drop_index("uq_accounts_user_active_name", table_name="accounts")
    op.drop_index("uq_transactions_user_source_ref", table_name="transactions")

    # ── 4. drop composite indexes ──
    op.drop_index(
        "ix_notification_events_user_status_trigger",
        table_name="notification_events",
    )
    op.drop_index(
        "ix_notification_rules_user_scope", table_name="notification_rules"
    )
    op.drop_index(
        "ix_custom_events_user_event_date", table_name="custom_events"
    )
    op.drop_index(
        "ix_bill_occurrences_user_status_due", table_name="bill_occurrences"
    )
    op.drop_index(
        "ix_bill_occurrences_user_due", table_name="bill_occurrences"
    )
    op.drop_index(
        "ix_recurring_bills_user_active", table_name="recurring_bills"
    )

    # ── 3. drop user_id from Phase 4 tables ──
    for table in reversed(_NEW_SCOPED_TABLES):
        op.drop_index(f"ix_{table}_user_id", table_name=table)
        op.drop_constraint(f"fk_{table}_user_id", table, type_="foreignkey")
        op.drop_column(table, "user_id")

    # ── 1. revert users table ──
    op.drop_constraint("uq_users_whatsapp_phone", "users", type_="unique")
    op.drop_constraint("uq_users_telegram_user_id", "users", type_="unique")
    op.drop_constraint("uq_users_shortcut_token", "users", type_="unique")
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.drop_constraint("ck_users_status", "users", type_="check")
    op.drop_column("users", "updated_at")
    op.drop_column("users", "status")
    op.drop_column("users", "whatsapp_phone")
    op.drop_column("users", "telegram_user_id")
    op.drop_column("users", "shortcut_token")
    op.drop_column("users", "locale")
    op.drop_column("users", "country")
    op.drop_column("users", "phone_number")
    op.drop_column("users", "email")
    op.alter_column("users", "full_name", new_column_name="name")
