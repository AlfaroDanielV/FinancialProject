from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


DashboardPeriod = Literal["month_current", "month_prev", "ytd", "last_6_months"]


class CategoryBreakdownItem(BaseModel):
    category: str
    income_total: Decimal
    expense_total: Decimal
    net_flow: Decimal


class CurrencyBalance(BaseModel):
    """A balance in a currency OTHER than the user's display currency (D3).

    The cross-account total never adds ₡+$ on a placeholder fx rate — each
    currency reconciles against its own accounts and is shown apart.
    """

    currency: str
    available: Decimal
    savings: Decimal


class DashboardSummary(BaseModel):
    period: DashboardPeriod
    display_currency: str
    income_total: Decimal
    expense_total: Decimal
    net_flow: Decimal
    savings_rate: Decimal | None
    balance_total: Decimal
    # Phase 7h: savings is "plata apartada" — the home screen shows
    # `available_balance` (spending accounts, savings EXCLUDED) as the figure
    # and `savings_balance` separately. `balance_total` stays for back-compat
    # (= display-currency total, savings included; D3 — other currencies live
    # in `other_currency_balances`, never added in).
    available_balance: Decimal
    savings_balance: Decimal
    # D3: balances in currencies OTHER than `display_currency`, shown apart
    # («(+ $Y en cuentas en dólares)»). Empty for single-currency users.
    other_currency_balances: list[CurrencyBalance] = []
    transaction_count: int
    transfer_rows_excluded: int
    accounts_count: int
    active_goals_count: int
    category_breakdown: list[CategoryBreakdownItem]


class CashFlowPoint(BaseModel):
    month: str
    income_total: Decimal
    expense_total: Decimal
    net_flow: Decimal


class CashFlowResponse(BaseModel):
    display_currency: str
    points: list[CashFlowPoint]


class DailyCashFlowPoint(BaseModel):
    date: str
    income_total: Decimal
    expense_total: Decimal
    net_flow: Decimal


class DailyCashFlowResponse(BaseModel):
    display_currency: str
    points: list[DailyCashFlowPoint]


class DashboardInsight(BaseModel):
    id: str
    insight_type: str
    group: str
    description: str
    confidence: Decimal
    source: str
    valid_until: str
    user_locked: bool
    updated_at: str


class DashboardInsightsResponse(BaseModel):
    items: list[DashboardInsight]
