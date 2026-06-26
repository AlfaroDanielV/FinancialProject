from pydantic import BaseModel


class OnboardingStatus(BaseModel):
    has_accounts: bool
    has_incomes: bool
    has_debts: bool
    has_recurring_bills: bool
    accounts_count: int
    incomes_count: int
    debts_count: int
    recurring_bills_count: int
    completeness_score: float  # 0.0..1.0 — LEGACY (kept for existing consumers)
    # Phase 8 B2: activation is the new "ready to use it daily" gate.
    # is_activated == has_accounts AND has_balance AND has_expense.
    is_activated: bool
    has_balance: bool
    has_expense: bool


class CategoriesResponse(BaseModel):
    categories: list[str]
