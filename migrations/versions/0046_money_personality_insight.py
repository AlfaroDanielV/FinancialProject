"""user_insights: money_personality computed insight type

Revision ID: 0046
Revises: 0045
Create Date: 2026-07-07 12:00:00.000000

Money personality classifier (Workstream E, 2026-07-07): the nightly computed
writer classifies each user's OWN ledger into one of four archetypes
(spender / avoider / saver / investor) via
`api/services/finance/money_personality.py` — deterministic, no LLM. It is
persisted as a Phase 6c COMPUTED insight (`money_personality`), so the
`ck_user_insights_type` CHECK (migration 0013) must admit the new type. It is
NOT LLM-extractable (two-writers rule) and NOT user-editable (a data-derived
label; `/olvidar` deletes it and it recomputes nightly). Singleton per user
(dedup_key='global'). See the vault note
`Decision - Money Personality Classifier (Computed Writer)`.

Sits on top of 0045 (auto-classification flags). No data change on upgrade.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0046"
down_revision: Union[str, None] = "0045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CONSTRAINT = "ck_user_insights_type"
_TABLE = "user_insights"

# The 14 original types (migration 0013) + the new money_personality.
_OLD_TYPES = (
    "spending_pattern", "recurring_drift", "cash_flow_stability", "debt_load",
    "emergency_fund", "cr_seasonal_pattern", "stated_preference", "stated_goal",
    "behavioral_flag", "archetype", "risk_posture", "decision_style",
    "financial_literacy", "stated_observed_gap",
)
_NEW_TYPES = _OLD_TYPES + ("money_personality",)


def _in_clause(types: tuple[str, ...]) -> str:
    joined = ",".join(f"'{t}'" for t in types)
    return f"insight_type IN ({joined})"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _in_clause(_NEW_TYPES))


def downgrade() -> None:
    # create_check_constraint re-validates existing rows, so the narrowed CHECK
    # would abort if any money_personality row exists. Purge them first (the
    # 0042/0033 safeguard) so the downgrade actually runs.
    op.execute(
        f"DELETE FROM {_TABLE} WHERE insight_type = 'money_personality'"
    )
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _in_clause(_OLD_TYPES))
