import { api } from "./client";

/**
 * Phase 8 B2: activation status. `is_activated` (1 account + a real balance + 1
 * expense) gates the first-run UX. `completeness_score` is legacy.
 */
export interface OnboardingStatus {
  has_accounts: boolean;
  has_incomes: boolean;
  has_debts: boolean;
  has_recurring_bills: boolean;
  accounts_count: number;
  incomes_count: number;
  debts_count: number;
  recurring_bills_count: number;
  completeness_score: number;
  is_activated: boolean;
  has_balance: boolean;
  has_expense: boolean;
}

export async function fetchOnboardingStatus(): Promise<OnboardingStatus> {
  const { data } = await api.get<OnboardingStatus>("/onboarding/status");
  return data;
}
