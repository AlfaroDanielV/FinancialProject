"""Read-only query tools for Phase 6a."""

from .accounts import register_account_tools
from .compare_periods import register_compare_periods_tool
from .debts import register_debt_tools
from .pending import register_pending_tools
from .recurring_bills import register_recurring_bill_tools
from .transactions import register_transaction_tools


def register_builtin_tools() -> None:
    register_transaction_tools()
    register_account_tools()
    register_recurring_bill_tools()
    register_debt_tools()
    register_pending_tools()
    register_compare_periods_tool()
