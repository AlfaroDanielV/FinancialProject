from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


DashboardPeriod = Literal["month_current", "month_prev", "ytd", "last_6_months"]


class CategoryBreakdownItem(BaseModel):
    category: str
    income_total: Decimal
    expense_total: Decimal
    net_flow: Decimal


class DashboardSummary(BaseModel):
    period: DashboardPeriod
    display_currency: str
    income_total: Decimal
    expense_total: Decimal
    net_flow: Decimal
    savings_rate: Decimal | None
    balance_total: Decimal
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
