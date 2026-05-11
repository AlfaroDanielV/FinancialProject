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
    completeness_score: float  # 0.0..1.0


class CategoriesResponse(BaseModel):
    categories: list[str]
