"""phase 7e: data foundation — advice trace, monthly snapshots, consent ledger

Revision ID: 0031
Revises: 0030
Create Date: 2026-06-11 00:00:00.000000

Three concerns, one goal: turn the deterministic engines from stateless
oracles into a labeled-dataset generator, and make consent a first-class,
versioned record before any second user exists. See vault
`Decision - Data Foundation (Advice Trace, Snapshots, Consent)`.

- `advice_events` — APPEND-ONLY. One row per deterministic verdict surfaced
  to the user (assess_purchase, savings capacity, goal feasibility gate, card
  never-payoff analysis, over-commitment nudge). Full input + result payloads
  (JSONB) per the "all calculations reproducible from stored inputs" rule.
  `kind` is deliberately NOT CHECK-constrained: advice surfaces grow with
  every phase and this is telemetry, not financial state — validation lives
  in `api/services/advice_trace.py`. `outcome_*` columns are written by a
  future labeling worker; semantics locked now (CHECK) so rows are forward-
  compatible.
- `envelope_snapshots` — month-end envelope execution history. Live figures
  (limits change, spend recomputes) are unrecoverable retroactively; the
  nightly upsert freezes them per period. Partial UNIQUE (envelope_id,
  period) makes recapture idempotent; envelope_id is SET NULL on hard delete
  so history survives the envelope.
- `cashflow_snapshots` — the MonthlyCashflow picture per period (income,
  committed, surplus, gate) + the envelope grand totals, UNIQUE(user_id,
  period).
- `user_consents` — APPEND-ONLY consent ledger. Current state = latest row
  per (user_id, purpose). Purposes ARE CHECK-constrained (compliance data;
  widen by migration like nudge_type in 0023).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── advice_events ─────────────────────────────────────────────────────────
    op.create_table(
        "advice_events",
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
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("verdict", sa.String(30), nullable=False),
        # Where the advice surfaced: query_tool | write_dispatcher |
        # nudge_evaluator (free-form; grows with surfaces).
        sa.Column("surface", sa.String(30), nullable=True),
        # Optional polymorphic subject (goal/debt/transaction/...). No FK on
        # purpose — the subject may be hard-deleted later; the trace survives.
        sa.Column("subject_type", sa.String(20), nullable=True),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        # Filled by a future outcome-labeling worker; pending until then.
        sa.Column(
            "outcome_status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("outcome", postgresql.JSONB(), nullable=True),
        sa.Column(
            "outcome_observed_at", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "outcome_status IN ('pending','followed','ignored','mixed','unknown')",
            name="ck_advice_events_outcome_status",
        ),
    )
    op.create_index(
        "ix_advice_events_user_created",
        "advice_events",
        ["user_id", sa.text("created_at DESC")],
    )
    op.create_index("ix_advice_events_kind", "advice_events", ["kind"])

    # ── envelope_snapshots ────────────────────────────────────────────────────
    op.create_table(
        "envelope_snapshots",
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
        sa.Column("period", sa.String(7), nullable=False),  # "YYYY-MM"
        sa.Column(
            "envelope_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("envelopes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Denormalized envelope identity so history survives hard deletes.
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("envelope_class", sa.String(16), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("depth", sa.SmallInteger(), nullable=False),
        # The summary figures, frozen (envelope's own currency, like the bars).
        sa.Column("limit_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("spent", sa.Numeric(12, 2), nullable=False),
        sa.Column("direct_spent", sa.Numeric(12, 2), nullable=False),
        sa.Column("reserved", sa.Numeric(12, 2), nullable=False),
        sa.Column("available", sa.Numeric(12, 2), nullable=False),
        sa.Column("over_limit", sa.Boolean(), nullable=False),
        sa.Column(
            "captured_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # Idempotent upsert key for live envelopes; SET-NULLed history rows fall
    # outside the partial index and are simply retained.
    op.create_index(
        "uq_envelope_snapshots_envelope_period",
        "envelope_snapshots",
        ["envelope_id", "period"],
        unique=True,
        postgresql_where=sa.text("envelope_id IS NOT NULL"),
    )
    op.create_index(
        "ix_envelope_snapshots_user_period",
        "envelope_snapshots",
        ["user_id", "period"],
    )

    # ── cashflow_snapshots ────────────────────────────────────────────────────
    op.create_table(
        "cashflow_snapshots",
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
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        # NULL = no income on file (honest unknown, mirrors income_known=False).
        sa.Column("monthly_income", sa.Numeric(14, 2), nullable=True),
        sa.Column("debt_payments", sa.Numeric(14, 2), nullable=False),
        sa.Column("recurring_bills", sa.Numeric(14, 2), nullable=False),
        sa.Column("envelope_allocations", sa.Numeric(14, 2), nullable=False),
        sa.Column("committed_outflows", sa.Numeric(14, 2), nullable=False),
        sa.Column("surplus", sa.Numeric(14, 2), nullable=False),
        sa.Column("savings_allocations", sa.Numeric(14, 2), nullable=False),
        sa.Column("gate_reason", sa.String(20), nullable=True),
        # Envelope grand totals (summary currency) — ties the snapshot to the
        # same figures the home-tab bars and hero showed that month.
        sa.Column("total_limit", sa.Numeric(14, 2), nullable=False),
        sa.Column("total_spent", sa.Numeric(14, 2), nullable=False),
        sa.Column("total_reserved", sa.Numeric(14, 2), nullable=False),
        sa.Column("total_available", sa.Numeric(14, 2), nullable=False),
        # Full reproducibility dump (by_class, unattached obligations, notes).
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "captured_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "user_id", "period", name="uq_cashflow_snapshots_user_period"
        ),
    )

    # ── user_consents ─────────────────────────────────────────────────────────
    op.create_table(
        "user_consents",
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
        sa.Column("purpose", sa.String(40), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        # Version of the consent text the user saw (e.g. "2026-06.v1").
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column(
            "source",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'api'"),
        ),
        sa.Column(
            "occurred_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "purpose IN ('core_service','behavioral_insights',"
            "'product_research','aggregated_datasets')",
            name="ck_user_consents_purpose",
        ),
        sa.CheckConstraint(
            "status IN ('granted','revoked')",
            name="ck_user_consents_status",
        ),
    )
    op.create_index(
        "ix_user_consents_user_purpose_occurred",
        "user_consents",
        ["user_id", "purpose", sa.text("occurred_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_consents_user_purpose_occurred", table_name="user_consents"
    )
    op.drop_table("user_consents")
    op.drop_table("cashflow_snapshots")
    op.drop_index(
        "ix_envelope_snapshots_user_period", table_name="envelope_snapshots"
    )
    op.drop_index(
        "uq_envelope_snapshots_envelope_period", table_name="envelope_snapshots"
    )
    op.drop_table("envelope_snapshots")
    op.drop_index("ix_advice_events_kind", table_name="advice_events")
    op.drop_index("ix_advice_events_user_created", table_name="advice_events")
    op.drop_table("advice_events")
