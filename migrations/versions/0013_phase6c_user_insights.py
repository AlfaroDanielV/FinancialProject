"""phase 6c: user_insights + user_insights_audit

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-07 00:00:00.000000

What this does:
    Creates the structured per-user memory layer for Phase 6c:

    - user_insights: typed, expirable insights with two writers
      (computed nightly, llm_extracted post-query). Content is JSONB
      validated app-side against per-type Pydantic models. Upsert by
      (user_id, insight_type, dedup_key). user_locked rows are sacred.

    - user_insights_audit: immutable timeline of every create / update /
      delete / reinforce / lock / unlock / export. payload jsonb is
      validated app-side against per-action Pydantic schemas (defined
      in schemas/insights.py).

Decisions locked in docs/phase-6c-decisions.md.

Schema specifics worth flagging:
    - 14 insight_type values enforced by CHECK. Adding a 15th requires
      a new migration + decisions doc entry.
    - source ∈ {computed, llm_extracted, user_override}. user_override
      is set when /editar_memoria writes; the audit row preserves the
      original source via payload.
    - dedup_key is TEXT, per-type semantics. See decisions doc table.
    - Partial index on valid_until skips user_locked rows (the
      lifecycle job can never expire them anyway).
    - No FK from audit.insight_id back to user_insights — when a row
      is hard-deleted, we want the audit timeline intact.

Rollback: drops both tables in reverse order. No data preserved —
insights are derived and can be recomputed from source data.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_VALID_INSIGHT_TYPES = (
    "spending_pattern",
    "recurring_drift",
    "cash_flow_stability",
    "debt_load",
    "emergency_fund",
    "cr_seasonal_pattern",
    "stated_preference",
    "stated_goal",
    "behavioral_flag",
    "archetype",
    "risk_posture",
    "decision_style",
    "financial_literacy",
    "stated_observed_gap",
)
_VALID_SOURCES = ("computed", "llm_extracted", "user_override")
_VALID_AUDIT_ACTIONS = (
    "created",
    "updated",
    "deleted",
    "reinforced",
    "locked",
    "unlocked",
    "exported",
)
_VALID_AUDIT_ACTORS = (
    "computed_worker",
    "llm_extractor",
    "user",
    "admin",
)


def _quoted_in_clause(values: Sequence[str]) -> str:
    """Render `('a','b','c')` for an IN clause inside a CHECK string."""
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    # ── user_insights ───────────────────────────────────────────────────────
    op.create_table(
        "user_insights",
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
        sa.Column("insight_type", sa.Text(), nullable=False),
        sa.Column(
            "content",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("valid_until", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "user_locked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("dedup_key", sa.Text(), nullable=False),
        sa.Column(
            "reinforcement_count",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "last_reinforced_at", sa.TIMESTAMP(timezone=True), nullable=True
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
            "confidence >= 0 AND confidence <= 1",
            name="ck_user_insights_confidence_range",
        ),
        sa.CheckConstraint(
            f"source IN {_quoted_in_clause(_VALID_SOURCES)}",
            name="ck_user_insights_source",
        ),
        sa.CheckConstraint(
            f"insight_type IN {_quoted_in_clause(_VALID_INSIGHT_TYPES)}",
            name="ck_user_insights_type",
        ),
        sa.UniqueConstraint(
            "user_id",
            "insight_type",
            "dedup_key",
            name="uq_user_insights_dedup",
        ),
    )

    # Hot path: get_user_context filters by user_id, then valid_until.
    op.create_index(
        "ix_user_insights_user_type_valid",
        "user_insights",
        ["user_id", "insight_type", sa.text("valid_until DESC")],
    )
    # Order-by index for the dispatcher's confidence-prioritized fetch.
    op.create_index(
        "ix_user_insights_user_confidence",
        "user_insights",
        ["user_id", sa.text("confidence DESC"), sa.text("updated_at DESC")],
    )
    # Lifecycle sweep index. Partial — locked rows can never be expired
    # by the job, so they don't need to be in this index at all.
    op.create_index(
        "ix_user_insights_valid_until_unlocked",
        "user_insights",
        ["valid_until"],
        postgresql_where=sa.text("user_locked = false"),
    )

    # ── user_insights_audit ─────────────────────────────────────────────────
    op.create_table(
        "user_insights_audit",
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
        # Nullable on purpose. When user_insights row is hard-deleted we
        # KEEP the audit row pointing at the (now-orphan) UUID so the
        # timeline is intact. No FK to user_insights.id either; the FK
        # would either CASCADE-delete the audit (bad) or block deletion
        # entirely (also bad). We accept the orphan UUID.
        sa.Column("insight_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"action IN {_quoted_in_clause(_VALID_AUDIT_ACTIONS)}",
            name="ck_user_insights_audit_action",
        ),
        sa.CheckConstraint(
            f"actor IN {_quoted_in_clause(_VALID_AUDIT_ACTORS)}",
            name="ck_user_insights_audit_actor",
        ),
    )

    # Read pattern: timeline for one user.
    op.create_index(
        "ix_user_insights_audit_user_time",
        "user_insights_audit",
        ["user_id", sa.text("created_at DESC")],
    )
    # Read pattern: history for one specific insight. Partial because
    # insight_id can be NULL for purge / export operations.
    op.create_index(
        "ix_user_insights_audit_insight",
        "user_insights_audit",
        ["insight_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("insight_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_insights_audit_insight", table_name="user_insights_audit"
    )
    op.drop_index(
        "ix_user_insights_audit_user_time", table_name="user_insights_audit"
    )
    op.drop_table("user_insights_audit")

    op.drop_index(
        "ix_user_insights_valid_until_unlocked", table_name="user_insights"
    )
    op.drop_index(
        "ix_user_insights_user_confidence", table_name="user_insights"
    )
    op.drop_index(
        "ix_user_insights_user_type_valid", table_name="user_insights"
    )
    op.drop_table("user_insights")
