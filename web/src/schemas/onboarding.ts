import { z } from "zod";

// Phase 6d B4: Zod mirrors of the API contracts in
// api/schemas/onboarding.py. Kept tight on purpose — adding fields here
// without updating the backend is a recipe for silent drift.

export const OnboardingStatus = z.object({
  has_accounts: z.boolean(),
  has_incomes: z.boolean(),
  has_debts: z.boolean(),
  has_recurring_bills: z.boolean(),
  accounts_count: z.number().int(),
  incomes_count: z.number().int(),
  debts_count: z.number().int(),
  recurring_bills_count: z.number().int(),
  completeness_score: z.number().min(0).max(1),
});
export type OnboardingStatus = z.infer<typeof OnboardingStatus>;

export const ExchangeResponse = z.object({
  user_id: z.string().uuid(),
  email: z.string(),
  full_name: z.string(),
});
export type ExchangeResponse = z.infer<typeof ExchangeResponse>;
