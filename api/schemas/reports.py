import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


class CategorySpend(BaseModel):
    category: str
    amount: float
    transaction_count: int


class BudgetSummary(BaseModel):
    category: str
    amount_limit: float
    spent: float
    remaining: float
    percent_used: float
    is_over_budget: bool


class GoalSummary(BaseModel):
    name: str
    target_amount: float
    current_amount: float
    progress_percent: float


class DebtProgressItem(BaseModel):
    name: str
    original_amount: float
    current_balance: float
    percent_paid: float
    estimated_payoff_date: Optional[date]


class UpcomingDueDate(BaseModel):
    name: str
    amount: float
    due_date: date
    days_until_due: int


class DebtReportOverview(BaseModel):
    total_owed: float
    total_minimum_monthly: float
    payments_made_this_week: int
    total_paid_this_week: float
    upcoming_due_dates: list[UpcomingDueDate]
    debt_progress: list[DebtProgressItem]


class WeeklyReportData(BaseModel):
    total_income: float
    total_expenses: float
    net: float
    transaction_count: int
    spend_by_category: list[CategorySpend]
    top_merchants: list[dict]
    budget_status: list[BudgetSummary]
    goal_progress: list[GoalSummary]
    debt_overview: Optional[DebtReportOverview] = None
    vs_last_week: Optional[dict] = None


class WeeklyReportResponse(BaseModel):
    id: uuid.UUID
    week_start: date
    week_end: date
    report_data: dict
    sent_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class GenerateReportRequest(BaseModel):
    week_start: Optional[date] = None
