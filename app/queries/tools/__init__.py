"""Read-only query tools for Phase 6a.

Phase 6c B11: `get_user_context` is gated on
`settings.insights_dispatcher_enabled`. With the flag off (default,
during the 7-day shadow window), the Sonnet dispatcher behaves
exactly as it did pre-B6 — same tool set, same prompt cache key.
The flag flips to True only after Daniel approves at the gate.

P10 B3: registration stays global/import-time, but each dispatch passes an
EXPLICIT allowlist to `list_tools_for_anthropic` — `BASE_TOOLSET` for a
normal turn (byte-identical to the pre-P10 wire order, test-locked) and
`ADVISORY_TOOLSET` for an advisory turn. This is the guard against the
global-registry trap: an advisory tool registered at import must NEVER leak
into the normal-mode tool list (it would fragment the prompt cache).
"""
from api.config import settings

from .accounts import register_account_tools
from .affordability import register_affordability_tools
from .compare_periods import register_compare_periods_tool
from .credit_cards import register_credit_card_tools
from .debts import register_debt_tools
from .envelopes import register_envelope_tools
from .financing import register_financing_tools
from .goals import register_goal_tools
from .advisory_planning import register_advisory_planning_tools
from .income import register_income_tools
from .net_worth import register_net_worth_tool
from .pending import register_pending_tools
from .retirement import register_retirement_tools
from .reallocation import register_reallocation_tools
from .salary import register_salary_tools
from .recurring_bills import register_recurring_bill_tools
from .transactions import register_transaction_tools
from .user_context import register_user_context_tool

# ── P10 B3: per-mode tool allowlists ─────────────────────────────────────────
# BASE_TOOLSET is TODAY's normal-mode set, in registry wire order. Do not add
# advisory tools here — the normal-mode list is byte-locked by
# tests/test_phase_10_b3_tool_scoping.py. `get_user_context` appears here but
# only ships when its flag registered it (the allowlist intersects the
# registry). compare_periods last = the cache breakpoint anchor.
BASE_TOOLSET: tuple[str, ...] = (
    "list_transactions",
    "aggregate_transactions",
    "list_unassigned_transactions",
    "get_account_balance",
    "list_accounts",
    "list_recurring_bills",
    "list_debts",
    "get_debt_details",
    "get_pending_confirmations",
    "get_envelope_spending",
    "suggest_reallocation_candidates",
    "assess_purchase",
    "get_savings_capacity",
    "assess_financing",
    "list_goals",
    "assess_goal",
    "list_registered_income",
    "compute_net_salary",
    "get_card_analysis",
    "get_user_context",
    "compare_periods",
)

# Advisory turns see everything the normal mode sees PLUS the assessment /
# framing tools. New advisory tools register in their modules (before
# compare_periods) and are added HERE, never to BASE_TOOLSET.
ADVISORY_ONLY_TOOLS: tuple[str, ...] = (
    "get_net_worth",           # B4
    "assess_counterfactual",   # B5
    "build_multiyear_plan",    # B5
    "project_retirement",      # B6
    "get_framing_principles",  # B9
)

ADVISORY_TOOLSET: tuple[str, ...] = BASE_TOOLSET + ADVISORY_ONLY_TOOLS


def register_builtin_tools() -> None:
    register_transaction_tools()
    register_account_tools()
    register_recurring_bill_tools()
    register_debt_tools()
    register_pending_tools()
    register_envelope_tools()
    register_reallocation_tools()
    register_affordability_tools()
    register_financing_tools()
    register_goal_tools()
    register_income_tools()
    register_salary_tools()
    register_credit_card_tools()
    # P10 advisory-only tools — registered globally, shipped ONLY via
    # ADVISORY_TOOLSET (never in the normal-mode wire list).
    register_net_worth_tool()
    register_advisory_planning_tools()
    register_retirement_tools()
    if settings.insights_dispatcher_enabled:
        register_user_context_tool()
    # compare_periods stays last so it remains the cache breakpoint anchor
    # (Phase 6c decision #8). Order is intentional — do not reorder.
    register_compare_periods_tool()
