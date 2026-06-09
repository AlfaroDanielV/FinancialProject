"""Read-only query tools for Phase 6a.

Phase 6c B11: `get_user_context` is gated on
`settings.insights_dispatcher_enabled`. With the flag off (default,
during the 7-day shadow window), the Sonnet dispatcher behaves
exactly as it did pre-B6 — same tool set, same prompt cache key.
The flag flips to True only after Daniel approves at the gate.
"""
from api.config import settings

from .accounts import register_account_tools
from .affordability import register_affordability_tools
from .compare_periods import register_compare_periods_tool
from .debts import register_debt_tools
from .envelopes import register_envelope_tools
from .financing import register_financing_tools
from .goals import register_goal_tools
from .pending import register_pending_tools
from .recurring_bills import register_recurring_bill_tools
from .transactions import register_transaction_tools
from .user_context import register_user_context_tool


def register_builtin_tools() -> None:
    register_transaction_tools()
    register_account_tools()
    register_recurring_bill_tools()
    register_debt_tools()
    register_pending_tools()
    register_envelope_tools()
    register_affordability_tools()
    register_financing_tools()
    register_goal_tools()
    if settings.insights_dispatcher_enabled:
        register_user_context_tool()
    # compare_periods stays last so it remains the cache breakpoint anchor
    # (Phase 6c decision #8). Order is intentional — do not reorder.
    register_compare_periods_tool()
