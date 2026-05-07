"""phase 6b: gmail ingestion foundation

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-05 00:00:00.000000

What this does:
    1. Creates four new tables for the Gmail OAuth + ingestion pipeline:
        - gmail_credentials (one row per user; refresh token stored only by
          name in Key Vault, not in this row)
        - bank_notification_samples (raw user-supplied examples used to
          calibrate the extractor and build the per-user sender whitelist)
        - gmail_messages_seen (idempotency log; one row per
          (user, gmail_message_id) so re-runs of the scanner don't double
          process)
        - gmail_ingestion_runs (audit of every backfill / daily / manual run)
    2. Extends `transactions` with three columns and a CHECK on `source`:
        - source CHECK adds 'gmail' and 'reconciled' to the existing values
          observed in production ('manual', 'shortcut', 'telegram'). The
          spec only listed (shortcut, gmail, manual, reconciled) but Phase
          5b shipped 'telegram' rows; rewriting them would lose origin
          signal. See docs/phase-6b-decisions.md.
        - gmail_message_id (nullable string + per-user UNIQUE partial
          index) — dedicated dedup column distinct from `source_ref` so
          Gmail dedup doesn't bleed into Phase 2 generic email parsing.
        - status CHECK ('confirmed','shadow','pending_review'), default
          'confirmed'. Orthogonal to `parse_status` (legacy). Shadow rows
          do NOT count toward balance until promoted by /aprobar_shadow.
    3. Adds activated_at column to gmail_credentials at create time
       (population happens at the end of onboarding, not at row insert).

Indexes are tuned for two hot paths:
    - Daily worker: list active users, then per user fetch
      (user_id, processed_at DESC) on gmail_messages_seen.
    - User-facing /estado_gmail: latest run per user
      (user_id, started_at DESC) on gmail_ingestion_runs.

Rollback drops everything in reverse order. Transaction column drops
will succeed even if rows have non-default values.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── gmail_credentials ───────────────────────────────────────────────────
    # One row per user. user_id is BOTH primary key and FK — a user has
    # exactly zero or one Gmail integration. The refresh token itself lives
    # in Key Vault under the name `kv_secret_name`; this row only knows
    # *where* to find it. `revoked_at` is set on /desconectar_gmail and
    # the row is kept (not deleted) for audit.
    op.create_table(
        "gmail_credentials",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("kv_secret_name", sa.String(255), nullable=False),
        sa.Column(
            "scopes",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY[]::text[]"),
        ),
        sa.Column(
            "granted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "activated_at", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column(
            "revoked_at", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column(
            "last_refresh_at", sa.TIMESTAMP(timezone=True), nullable=True
        ),
    )
    # Active users for the daily worker — partial so we don't index revoked
    # rows we never want to touch again.
    op.create_index(
        "ix_gmail_credentials_active",
        "gmail_credentials",
        ["user_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    # ── bank_notification_samples ───────────────────────────────────────────
    # Raw text the user paste/photographed during onboarding. Used to seed
    # the whitelist (`detected_sender`) and to remember which formats the
    # extractor has already been calibrated against. Confidence is the
    # analyzer's self-reported score on (sender, bank, format).
    op.create_table(
        "bank_notification_samples",
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
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("detected_sender", sa.String(320), nullable=True),
        sa.Column("detected_bank", sa.String(64), nullable=True),
        sa.Column(
            "detected_format",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "source IN ('photo','text')",
            name="ck_bank_notification_samples_source",
        ),
    )
    op.create_index(
        "ix_bank_notification_samples_user_created",
        "bank_notification_samples",
        ["user_id", sa.text("created_at DESC")],
    )

    # ── gmail_messages_seen ─────────────────────────────────────────────────
    # Idempotency log. Composite PK (user_id, gmail_message_id) means the
    # scanner can re-run the same Gmail query without re-processing.
    # Outcome is the terminal state of one message in one run; if a message
    # is re-evaluated (e.g. because user un-rejected it) we INSERT a fresh
    # row in a follow-up table, not UPDATE here. Today there's no such
    # follow-up — re-evaluation isn't supported. PK enforces "process once".
    op.create_table(
        "gmail_messages_seen",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("gmail_message_id", sa.String(128), nullable=False),
        sa.Column(
            "processed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column(
            "transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "ingestion_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "error",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "gmail_message_id", name="pk_gmail_messages_seen"
        ),
        sa.CheckConstraint(
            "outcome IN ('matched','created','created_shadow',"
            "'skipped','failed','rejected_by_user')",
            name="ck_gmail_messages_seen_outcome",
        ),
    )
    op.create_index(
        "ix_gmail_messages_seen_user_processed",
        "gmail_messages_seen",
        ["user_id", sa.text("processed_at DESC")],
    )
    op.create_index(
        "ix_gmail_messages_seen_run",
        "gmail_messages_seen",
        ["ingestion_run_id"],
    )

    # ── gmail_ingestion_runs ────────────────────────────────────────────────
    op.create_table(
        "gmail_ingestion_runs",
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
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "finished_at", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column(
            "messages_scanned",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "transactions_created",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "transactions_matched",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "errors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.CheckConstraint(
            "mode IN ('backfill','daily','manual')",
            name="ck_gmail_ingestion_runs_mode",
        ),
    )
    op.create_index(
        "ix_gmail_ingestion_runs_user_started",
        "gmail_ingestion_runs",
        ["user_id", sa.text("started_at DESC")],
    )

    # FK from gmail_messages_seen.ingestion_run_id → gmail_ingestion_runs.id.
    # Created here (not inline) because gmail_ingestion_runs didn't exist
    # when gmail_messages_seen was defined above.
    op.create_foreign_key(
        "fk_gmail_messages_seen_run",
        source_table="gmail_messages_seen",
        referent_table="gmail_ingestion_runs",
        local_cols=["ingestion_run_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )

    # ── transactions: extend source, add gmail_message_id + status ─────────
    op.add_column(
        "transactions",
        sa.Column("gmail_message_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="confirmed",
        ),
    )
    op.create_check_constraint(
        "ck_transactions_status",
        "transactions",
        "status IN ('confirmed','shadow','pending_review')",
    )
    op.create_check_constraint(
        "ck_transactions_source",
        "transactions",
        "source IN ('manual','shortcut','telegram','gmail','reconciled')",
    )
    # Per-user UNIQUE on gmail_message_id, partial so it only enforces
    # uniqueness on rows that actually have a Gmail origin. Two users can
    # share a message id (won't happen in practice but the constraint is
    # honest about scope).
    op.create_index(
        "uq_transactions_user_gmail_message",
        "transactions",
        ["user_id", "gmail_message_id"],
        unique=True,
        postgresql_where=sa.text("gmail_message_id IS NOT NULL"),
    )
    # Index for the reconciler's lookup: "transactions in user's last 7d
    # without a gmail_message_id". Partial keeps it small.
    op.create_index(
        "ix_transactions_recon_candidates",
        "transactions",
        ["user_id", "transaction_date"],
        postgresql_where=sa.text("gmail_message_id IS NULL"),
    )
    # Index for shadow listing.
    op.create_index(
        "ix_transactions_user_status",
        "transactions",
        ["user_id", "status"],
        postgresql_where=sa.text("status = 'shadow'"),
    )


def downgrade() -> None:
    # transactions extensions ────────────────────────────────────────────────
    op.drop_index("ix_transactions_user_status", table_name="transactions")
    op.drop_index(
        "ix_transactions_recon_candidates", table_name="transactions"
    )
    op.drop_index(
        "uq_transactions_user_gmail_message", table_name="transactions"
    )
    op.drop_constraint(
        "ck_transactions_source", "transactions", type_="check"
    )
    op.drop_constraint(
        "ck_transactions_status", "transactions", type_="check"
    )
    op.drop_column("transactions", "status")
    op.drop_column("transactions", "gmail_message_id")

    # gmail_messages_seen FK to gmail_ingestion_runs ─────────────────────────
    op.drop_constraint(
        "fk_gmail_messages_seen_run",
        "gmail_messages_seen",
        type_="foreignkey",
    )

    # gmail_ingestion_runs ──────────────────────────────────────────────────
    op.drop_index(
        "ix_gmail_ingestion_runs_user_started",
        table_name="gmail_ingestion_runs",
    )
    op.drop_table("gmail_ingestion_runs")

    # gmail_messages_seen ───────────────────────────────────────────────────
    op.drop_index(
        "ix_gmail_messages_seen_run", table_name="gmail_messages_seen"
    )
    op.drop_index(
        "ix_gmail_messages_seen_user_processed",
        table_name="gmail_messages_seen",
    )
    op.drop_table("gmail_messages_seen")

    # bank_notification_samples ─────────────────────────────────────────────
    op.drop_index(
        "ix_bank_notification_samples_user_created",
        table_name="bank_notification_samples",
    )
    op.drop_table("bank_notification_samples")

    # gmail_credentials ─────────────────────────────────────────────────────
    op.drop_index(
        "ix_gmail_credentials_active", table_name="gmail_credentials"
    )
    op.drop_table("gmail_credentials")
